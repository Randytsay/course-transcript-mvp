"""Strict, read-only rclone source browsing and inspection."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from app.jobs.rclone_auth import rclone_environment


MEDIA_EXTENSIONS = frozenset(
    {
        ".aac",
        ".avi",
        ".flac",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mts",
        ".ogg",
        ".wav",
        ".webm",
    }
)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
SelectionMode = Literal["files", "folder"]
LOGGER = logging.getLogger(__name__)

# Browsing the same folder repeatedly is common in the web UI. Keep a short,
# process-local cache so moving into a child directory and returning does not
# immediately trigger another Google Drive request. Entries are immutable
# dataclasses and are safe to share between callers.
_DIRECTORY_CACHE: dict[str, tuple[float, tuple["DriveEntry", ...]]] = {}
_DIRECTORY_CACHE_LOCK = threading.Lock()
_DIRECTORY_CACHE_MAX_ITEMS = 128


class SourceInspectionError(ValueError):
    """Raised when a source path is unsafe, missing, or unsupported."""


@dataclass(frozen=True)
class SourceMetadata:
    source_path: str
    name: str
    size_bytes: int
    modified_at: str | None
    mime_type: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True)
class DriveEntry:
    source_path: str
    name: str
    is_dir: bool
    size_bytes: int
    modified_at: str | None
    mime_type: str | None
    supported_media: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "name": self.name,
            "is_dir": self.is_dir,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "mime_type": self.mime_type,
            "supported_media": self.supported_media,
        }


def _allowed_prefix() -> str:
    return os.environ.get("COURSE_TRANSCRIPT_ALLOWED_SOURCE_PREFIX", "gdrive:")


def _cache_ttl_seconds() -> float:
    raw = os.environ.get("COURSE_TRANSCRIPT_DRIVE_BROWSE_CACHE_SECONDS", "60")
    try:
        return max(0.0, min(float(raw), 600.0))
    except ValueError:
        return 60.0


def _path_fingerprint(path: str) -> str:
    """Return a non-reversible identifier suitable for performance logs."""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]


def _clear_directory_cache() -> None:
    """Clear the process cache. Intended for tests and controlled refreshes."""
    with _DIRECTORY_CACHE_LOCK:
        _DIRECTORY_CACHE.clear()


def _validate_common_path(source_path: str, allowed_prefix: str) -> tuple[str, str]:
    candidate = source_path.strip()
    if not candidate or CONTROL_CHARACTERS.search(candidate):
        raise SourceInspectionError("來源路徑不可為空或包含控制字元")
    if not allowed_prefix or not candidate.startswith(allowed_prefix):
        raise SourceInspectionError("來源路徑不在允許的 Google Drive 範圍")

    relative = candidate[len(allowed_prefix) :].strip("/")
    path = PurePosixPath(relative)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SourceInspectionError("來源路徑格式不合法")
    root = allowed_prefix.rstrip("/")
    separator = "" if root.endswith(":") else "/"
    normalized = root if not relative else f"{root}{separator}{relative}"
    return normalized, relative


def validate_source_path(source_path: str, allowed_prefix: str) -> str:
    candidate, relative = _validate_common_path(source_path, allowed_prefix)
    if source_path.strip().endswith("/") or not relative:
        raise SourceInspectionError("來源必須是單一檔案，不可為資料夾")
    if PurePosixPath(relative).suffix.lower() not in MEDIA_EXTENSIONS:
        raise SourceInspectionError("來源副檔名不在允許的影音格式清單")
    return candidate


def validate_directory_path(source_path: str, allowed_prefix: str) -> str:
    candidate, relative = _validate_common_path(source_path, allowed_prefix)
    if relative and PurePosixPath(relative).suffix.lower() in MEDIA_EXTENSIONS:
        raise SourceInspectionError("資料夾路徑不可指向影音檔")
    return candidate


def _run_lsjson(
    candidate: str,
    *,
    recursive: bool,
    files_only: bool = False,
    timeout_seconds: int = 45,
) -> list[dict[str, object]] | dict[str, object]:
    command = ["rclone", "lsjson"]
    if recursive:
        command.append("--recursive")
        if os.environ.get("COURSE_TRANSCRIPT_RCLONE_FAST_LIST", "false").lower() in {
            "1",
            "true",
            "yes",
        }:
            command.append("--fast-list")
    if files_only:
        command.append("--files-only")
    command.extend(["--no-mimetype", candidate])
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=rclone_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceInspectionError("無法在期限內檢查 Drive 來源") from exc
    if result.returncode != 0:
        raise SourceInspectionError("Drive 來源不存在或目前無法讀取")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SourceInspectionError("rclone 回傳無法驗證的資料") from exc
    if not isinstance(payload, (dict, list)):
        raise SourceInspectionError("rclone 回傳格式不正確")
    return payload


def _join_remote_path(parent: str, child: str) -> str:
    root = parent.rstrip("/")
    separator = "" if root.endswith(":") else "/"
    return f"{root}{separator}{child.lstrip('/')}"


def _parent_remote_path(source_path: str) -> str:
    allowed_root = _allowed_prefix().rstrip("/")
    relative = source_path[len(allowed_root) :].strip("/")
    parent_relative = str(PurePosixPath(relative).parent)
    if parent_relative in {"", "."}:
        return allowed_root
    return _join_remote_path(allowed_root, parent_relative)


def _cache_get(directory: str) -> list[DriveEntry] | None:
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _DIRECTORY_CACHE_LOCK:
        cached = _DIRECTORY_CACHE.get(directory)
        if cached is None:
            return None
        created_at, entries = cached
        if now - created_at > ttl:
            _DIRECTORY_CACHE.pop(directory, None)
            return None
        return list(entries)


def _cache_put(directory: str, entries: list[DriveEntry]) -> None:
    if _cache_ttl_seconds() <= 0:
        return
    now = time.monotonic()
    with _DIRECTORY_CACHE_LOCK:
        expired = [
            key
            for key, (created_at, _) in _DIRECTORY_CACHE.items()
            if now - created_at > _cache_ttl_seconds()
        ]
        for key in expired:
            _DIRECTORY_CACHE.pop(key, None)
        if len(_DIRECTORY_CACHE) >= _DIRECTORY_CACHE_MAX_ITEMS:
            oldest = min(
                _DIRECTORY_CACHE,
                key=lambda key: _DIRECTORY_CACHE[key][0],
            )
            _DIRECTORY_CACHE.pop(oldest, None)
        _DIRECTORY_CACHE[directory] = (now, tuple(entries))


def list_rclone_directory(source_path: str) -> tuple[str, list[DriveEntry]]:
    """List one Drive directory level without exposing rclone configuration."""
    directory = validate_directory_path(source_path, _allowed_prefix())
    started = time.monotonic()
    cached = _cache_get(directory)
    if cached is not None:
        LOGGER.info(
            "drive_browse cache_hit=true path_id=%s items=%d duration_ms=%d",
            _path_fingerprint(directory),
            len(cached),
            round((time.monotonic() - started) * 1000),
        )
        return directory, cached

    payload = _run_lsjson(directory, recursive=False)
    if not isinstance(payload, list):
        raise SourceInspectionError("Drive 資料夾回傳格式不正確")
    maximum_entries = int(
        os.environ.get("COURSE_TRANSCRIPT_DRIVE_BROWSE_MAX_ENTRIES", "500")
    )
    if len(payload) > maximum_entries:
        raise SourceInspectionError(
            f"此資料夾超過 {maximum_entries} 個項目，請進入較小的子資料夾"
        )

    entries: list[DriveEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        remote_path = str(item.get("Path") or name).strip("/")
        if not name or not remote_path:
            continue
        is_dir = bool(item.get("IsDir"))
        entries.append(
            DriveEntry(
                source_path=_join_remote_path(directory, remote_path),
                name=name,
                is_dir=is_dir,
                size_bytes=0 if is_dir else max(0, int(item.get("Size", 0) or 0)),
                modified_at=str(item.get("ModTime")) if item.get("ModTime") else None,
                mime_type=str(item.get("MimeType")) if item.get("MimeType") else None,
                supported_media=(
                    not is_dir
                    and PurePosixPath(name).suffix.lower() in MEDIA_EXTENSIONS
                ),
            )
        )
    entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
    _cache_put(directory, entries)
    LOGGER.info(
        "drive_browse cache_hit=false path_id=%s items=%d duration_ms=%d",
        _path_fingerprint(directory),
        len(entries),
        round((time.monotonic() - started) * 1000),
    )
    return directory, entries


def _metadata_from_directory_entries(source_paths: list[str]) -> list[SourceMetadata]:
    """Resolve explicit files by listing each parent folder once.

    The browser already navigates these folders, so the short listing cache will
    usually satisfy this without another Drive request. If a folder is too
    large, unavailable, or a file changed, the caller falls back to an exact
    stat for only the unresolved file.
    """
    ordered_candidates = [
        validate_source_path(path, _allowed_prefix()) for path in source_paths
    ]
    by_parent: dict[str, list[str]] = {}
    for candidate in ordered_candidates:
        by_parent.setdefault(_parent_remote_path(candidate), []).append(candidate)

    resolved: dict[str, SourceMetadata] = {}
    for parent, candidates in by_parent.items():
        try:
            _, entries = list_rclone_directory(parent)
        except SourceInspectionError:
            entries = []
        entries_by_path = {entry.source_path: entry for entry in entries}
        for candidate in candidates:
            entry = entries_by_path.get(candidate)
            if (
                entry is not None
                and not entry.is_dir
                and entry.supported_media
                and entry.size_bytes > 0
            ):
                resolved[candidate] = SourceMetadata(
                    source_path=candidate,
                    name=entry.name,
                    size_bytes=entry.size_bytes,
                    modified_at=entry.modified_at,
                    mime_type=entry.mime_type,
                )

    return [
        resolved.get(candidate) or inspect_rclone_source(candidate)
        for candidate in ordered_candidates
    ]


def inspect_rclone_selection(
    *,
    selection_mode: SelectionMode,
    source_paths: list[str],
) -> list[SourceMetadata]:
    """Resolve explicit files or one folder recursively into media metadata."""
    maximum_files = int(os.environ.get("COURSE_TRANSCRIPT_BATCH_MAX_FILES", "100"))
    if selection_mode == "files":
        unique_paths = list(dict.fromkeys(source_paths))
        if not unique_paths:
            raise SourceInspectionError("至少選擇一個影音檔")
        if len(unique_paths) > maximum_files:
            raise SourceInspectionError(f"單一批次最多 {maximum_files} 個影音檔")
        return _metadata_from_directory_entries(unique_paths)

    if len(source_paths) != 1:
        raise SourceInspectionError("整個資料夾模式一次只能指定一個資料夾")
    directory = validate_directory_path(source_paths[0], _allowed_prefix())
    if directory == _allowed_prefix().rstrip("/"):
        raise SourceInspectionError("不可直接選取整個 Drive 根目錄，請先進入課程資料夾")
    payload = _run_lsjson(
        directory, recursive=True, files_only=True, timeout_seconds=90
    )
    if not isinstance(payload, list):
        raise SourceInspectionError("Drive 資料夾回傳格式不正確")
    media_items = [
        item
        for item in payload
        if isinstance(item, dict)
        and PurePosixPath(str(item.get("Name") or item.get("Path") or ""))
        .suffix.lower()
        in MEDIA_EXTENSIONS
    ]
    if not media_items:
        raise SourceInspectionError("此資料夾內找不到支援的影音檔")
    if len(media_items) > maximum_files:
        raise SourceInspectionError(
            f"此資料夾包含 {len(media_items)} 個影音檔，"
            f"超過單批 {maximum_files} 個限制"
        )

    resolved: list[SourceMetadata] = []
    for item in media_items:
        remote_path = str(item.get("Path") or item.get("Name") or "").strip("/")
        size = int(item.get("Size", 0) or 0)
        if not remote_path or size <= 0:
            continue
        resolved.append(
            SourceMetadata(
                source_path=_join_remote_path(directory, remote_path),
                name=PurePosixPath(remote_path).name,
                size_bytes=size,
                modified_at=str(item.get("ModTime")) if item.get("ModTime") else None,
                mime_type=str(item.get("MimeType")) if item.get("MimeType") else None,
            )
        )
    if not resolved:
        raise SourceInspectionError("資料夾中的影音檔均無有效檔案大小")
    resolved.sort(key=lambda item: item.source_path.casefold())
    return resolved


def inspect_rclone_source(source_path: str) -> SourceMetadata:
    candidate = validate_source_path(source_path, _allowed_prefix())
    command = [
        "rclone",
        "lsjson",
        "--stat",
        "--files-only",
        "--no-mimetype",
        candidate,
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=rclone_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceInspectionError("無法在期限內檢查 Drive 來源") from exc
    if result.returncode != 0:
        raise SourceInspectionError("Drive 來源不存在或目前無法讀取")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SourceInspectionError("rclone 回傳無法驗證的資料") from exc
    if not isinstance(payload, dict) or payload.get("IsDir"):
        raise SourceInspectionError("來源必須是可讀取的單一檔案")
    size = int(payload.get("Size", -1))
    if size <= 0:
        raise SourceInspectionError("來源檔案大小無效")
    return SourceMetadata(
        source_path=candidate,
        name=PurePosixPath(candidate).name,
        size_bytes=size,
        modified_at=str(payload.get("ModTime")) if payload.get("ModTime") else None,
        mime_type=str(payload.get("MimeType")) if payload.get("MimeType") else None,
    )
