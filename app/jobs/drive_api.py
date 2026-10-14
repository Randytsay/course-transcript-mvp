"""Read-only Google Drive API browser with rclone-compatible paths.

Interactive listing/search uses Drive API file IDs internally while preserving
``gdrive:path`` values for the existing rclone transfer workers. OAuth access
tokens are refreshed in memory; mounted secrets are never rewritten.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.jobs.source import DriveEntry, MEDIA_EXTENSIONS, SourceInspectionError

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_TOKEN_LOCK = threading.RLock()
_CACHE_LOCK = threading.RLock()
_MAX_PARENT_DEPTH = 100


class DriveApiError(SourceInspectionError):
    """Safe Google Drive API error surfaced to the web boundary."""


@dataclass(frozen=True)
class DriveApiPage:
    current_path: str
    parent_path: str | None
    entries: list[DriveEntry]
    next_page_token: str | None
    provider: str = "google_api"


@dataclass
class _OAuthState:
    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str | None = None
    expires_at: float = 0.0


_OAUTH_STATE: _OAuthState | None = None
_PATH_ID_CACHE: dict[str, str] = {"gdrive:": "root"}
_ID_PATH_CACHE: dict[str, str] = {"root": "gdrive:"}


def _remote_name() -> str:
    return os.environ.get("COURSE_TRANSCRIPT_DRIVE_REMOTE", "gdrive").strip() or "gdrive"


def _root_path() -> str:
    return f"{_remote_name()}:"


def _config_path() -> str:
    return os.environ.get("RCLONE_CONFIG", "/run/secrets/rclone.conf")


def _read_refresh_token_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
    except OSError as exc:
        raise DriveApiError("Google Drive OAuth refresh token 檔案無法讀取") from exc
    if not raw:
        raise DriveApiError("Google Drive OAuth refresh token 為空")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict):
        token = str(payload.get("refresh_token") or payload.get("refreshToken") or "").strip()
        if token:
            return token
    raise DriveApiError("Google Drive OAuth refresh token 格式不正確")


def _load_oauth_state() -> _OAuthState:
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "").strip()
    refresh_file = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN_FILE", "").strip()
    if refresh_file and not refresh_token:
        refresh_token = _read_refresh_token_file(refresh_file)
    if client_id and client_secret and refresh_token:
        return _OAuthState(client_id, client_secret, refresh_token)

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(_config_path(), "r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except OSError as exc:
        raise DriveApiError("Google Drive OAuth 設定尚未掛載") from exc
    section = _remote_name()
    if not parser.has_section(section):
        raise DriveApiError(f"rclone remote [{section}] 不存在")

    client_id = client_id or parser.get(section, "client_id", fallback="").strip()
    client_secret = client_secret or parser.get(section, "client_secret", fallback="").strip()
    token_raw = parser.get(section, "token", fallback="").strip()
    if token_raw:
        try:
            token_payload = json.loads(token_raw)
        except json.JSONDecodeError as exc:
            raise DriveApiError("rclone OAuth token 格式無法解析") from exc
        refresh_token = refresh_token or str(token_payload.get("refresh_token") or "").strip()
    if not client_id or not client_secret or not refresh_token:
        raise DriveApiError("Google Drive API 需要自訂 OAuth client_id、client_secret 與 refresh_token")
    return _OAuthState(client_id, client_secret, refresh_token)


def _oauth_state() -> _OAuthState:
    global _OAUTH_STATE
    with _TOKEN_LOCK:
        if _OAUTH_STATE is None:
            _OAUTH_STATE = _load_oauth_state()
        return _OAUTH_STATE


def clear_drive_path_cache() -> None:
    """Clear only navigation caches while retaining a valid access token."""
    root = _root_path()
    with _CACHE_LOCK:
        _PATH_ID_CACHE.clear()
        _PATH_ID_CACHE[root] = "root"
        _ID_PATH_CACHE.clear()
        _ID_PATH_CACHE["root"] = root


def reset_drive_api_state() -> None:
    """Reset OAuth and path state for tests or credential rotation."""
    global _OAUTH_STATE
    with _TOKEN_LOCK:
        _OAUTH_STATE = None
    clear_drive_path_cache()


def _refresh_access_token() -> str:
    state = _oauth_state()
    with _TOKEN_LOCK:
        now = time.time()
        if state.access_token and state.expires_at - 60 > now:
            return state.access_token
        body = urllib.parse.urlencode({
            "client_id": state.client_id,
            "client_secret": state.client_secret,
            "refresh_token": state.refresh_token,
            "grant_type": "refresh_token",
        }).encode("utf-8")
        request = urllib.request.Request(
            _TOKEN_ENDPOINT,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 401}:
                raise DriveApiError("Google Drive OAuth 授權已失效，請重新授權") from exc
            raise DriveApiError(f"Google Drive OAuth 服務回應錯誤 ({exc.code})") from exc
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise DriveApiError("無法連線 Google Drive OAuth 服務") from exc
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise DriveApiError("Google Drive OAuth 未回傳 access token")
        state.access_token = token
        state.expires_at = now + max(60, int(payload.get("expires_in") or 3600))
        return token


def _api_json(path: str, params: dict[str, str | int | bool] | None = None) -> dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{_DRIVE_API}{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {_refresh_access_token()}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            reset_drive_api_state()
            raise DriveApiError("Google Drive OAuth 授權已失效，請重新授權") from exc
        if exc.code == 403:
            raise DriveApiError("Google Drive API 權限不足或配額受限") from exc
        if exc.code == 404:
            raise DriveApiError("Google Drive 項目不存在或無權存取") from exc
        raise DriveApiError(f"Google Drive API 回應錯誤 ({exc.code})") from exc
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise DriveApiError("Google Drive API 目前無法連線") from exc
    if not isinstance(payload, dict):
        raise DriveApiError("Google Drive API 回傳格式不正確")
    return payload


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _normalize_path(source_path: str) -> str:
    value = source_path.strip().rstrip("/")
    root = _root_path()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DriveApiError("Google Drive 路徑包含不允許的控制字元")
    if value == root:
        return root
    if not value.startswith(root):
        raise DriveApiError("來源路徑不在允許的 Google Drive 範圍")
    parts = PurePosixPath(value[len(root):].strip("/")).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise DriveApiError("Google Drive 路徑格式不正確")
    return f"{root}{'/'.join(parts)}"


def _root_folder_id() -> str:
    configured = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
    if configured:
        return configured
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(_config_path(), "r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except OSError:
        return "root"
    return parser.get(_remote_name(), "root_folder_id", fallback="root").strip() or "root"


def _remember(path: str, file_id: str) -> None:
    with _CACHE_LOCK:
        existing = _PATH_ID_CACHE.get(path)
        if existing and existing != file_id:
            raise DriveApiError(f"Google Drive 同一路徑存在重複名稱，無法安全選取：{path}")
        _PATH_ID_CACHE[path] = file_id
        _ID_PATH_CACHE[file_id] = path


def _folder_path_by_id(folder_id: str, trail: frozenset[str] = frozenset()) -> str:
    root_id = _root_folder_id()
    if folder_id in {"root", root_id}:
        return _root_path()
    with _CACHE_LOCK:
        cached = _ID_PATH_CACHE.get(folder_id)
    if cached:
        return cached
    if folder_id in trail or len(trail) >= _MAX_PARENT_DEPTH:
        raise DriveApiError("Google Drive 父資料夾結構循環或過深")
    item = _api_json(
        f"/files/{urllib.parse.quote(folder_id, safe='')}",
        {"fields": "id,name,mimeType,parents", "supportsAllDrives": "true"},
    )
    if str(item.get("mimeType") or "") != _FOLDER_MIME:
        raise DriveApiError("Google Drive 父項目不是資料夾")
    name = str(item.get("name") or "").strip()
    parents = item.get("parents")
    if not name or not isinstance(parents, list) or not parents:
        raise DriveApiError("Google Drive 項目不在目前允許的根目錄下")
    parent_path = _folder_path_by_id(str(parents[0]), trail | {folder_id})
    path = f"{parent_path.rstrip('/')}/{name}" if parent_path != _root_path() else f"{_root_path()}{name}"
    _remember(path, folder_id)
    return path


def _resolve_path_id(source_path: str) -> str:
    normalized = _normalize_path(source_path)
    with _CACHE_LOCK:
        cached = _PATH_ID_CACHE.get(normalized)
    if cached:
        return _root_folder_id() if cached == "root" else cached
    current_id = _root_folder_id()
    current_path = _root_path()
    for part in PurePosixPath(normalized[len(_root_path()):].strip("/")).parts:
        payload = _api_json("/files", {
            "q": (
                f"'{_escape_query(current_id)}' in parents and "
                f"name = '{_escape_query(part)}' and mimeType = '{_FOLDER_MIME}' and trashed = false"
            ),
            "pageSize": 2,
            "fields": "files(id,name)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        })
        files = payload.get("files")
        if not isinstance(files, list) or len(files) != 1:
            raise DriveApiError(f"Google Drive 資料夾路徑無法唯一解析：{part}")
        current_id = str(files[0].get("id") or "")
        if not current_id:
            raise DriveApiError("Google Drive 資料夾缺少識別碼")
        current_path = f"{current_path.rstrip('/')}/{part}" if current_path != _root_path() else f"{_root_path()}{part}"
        _remember(current_path, current_id)
    return current_id


_DRIVE_METADATA_FIELDS = (
    "id,name,mimeType,size,modifiedTime,version,md5Checksum,trashed,parents"
)


def _drive_media_metadata(source_path: str) -> dict[str, Any]:
    normalized = _normalize_path(source_path)
    relative = normalized[len(_root_path()):].strip("/")
    parts = PurePosixPath(relative).parts
    if not parts:
        raise DriveApiError("請指定 Google Drive 音訊檔案路徑")
    name = parts[-1]
    if PurePosixPath(name).suffix.lower() not in MEDIA_EXTENSIONS:
        raise DriveApiError("來源檔案格式不在支援的媒體清單")

    # Navigation caches are an optimization for browsing, not evidence that a
    # selected path still resolves to the same Drive folder.
    clear_drive_path_cache()
    parent_path = _root_path() + "/".join(parts[:-1]) if len(parts) > 1 else _root_path()
    parent_id = _resolve_path_id(parent_path)
    listing = _api_json("/files", {
        "q": (
            f"'{_escape_query(parent_id)}' in parents and "
            f"name = '{_escape_query(name)}' and trashed = false"
        ),
        "pageSize": 2,
        "fields": "files(id,name)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    })
    files = listing.get("files")
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise DriveApiError("Google Drive 音訊檔案路徑無法唯一解析")
    file_id = str(files[0].get("id") or "").strip()
    if not file_id:
        raise DriveApiError("Google Drive 音訊檔案缺少識別碼")
    item = _api_json(
        f"/files/{urllib.parse.quote(file_id, safe='')}",
        {"fields": _DRIVE_METADATA_FIELDS, "supportsAllDrives": "true"},
    )
    if (str(item.get("id") or "") != file_id
            or str(item.get("name") or "") != name
            or not isinstance(item.get("parents"), list)
            or parent_id not in item["parents"]):
        raise DriveApiError("Google Drive 音訊檔案路徑在檢查期間已變更")
    return _validate_drive_media_metadata(item)


def read_drive_media_metadata(source_path: str) -> dict[str, Any]:
    """Re-resolve an exact media path and return fresh, read-only Drive metadata.

    This performs Drive GET requests only. It deliberately does not reuse the
    browsing path cache as evidence that a previously approved source is still
    the same object.
    """
    return _drive_media_metadata(source_path)


def _validate_drive_media_metadata(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("trashed") is True:
        raise DriveApiError("Google Drive 音訊檔案已移至垃圾桶")
    if str(item.get("mimeType") or "").startswith("application/vnd.google-apps."):
        raise DriveApiError("Google Drive 原生文件不是可下載的音訊來源")
    file_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    version = str(item.get("version") or "").strip()
    md5 = str(item.get("md5Checksum") or "").lower()
    parents = item.get("parents")
    try:
        size = int(item.get("size"))
    except (TypeError, ValueError) as exc:
        raise DriveApiError("Google Drive 音訊檔案缺少有效大小") from exc
    if (not file_id or not name or not version or size <= 0
            or not re.fullmatch(r"[0-9a-f]{32}", md5)
            or not isinstance(parents, list) or not parents):
        raise DriveApiError("Google Drive 音訊檔案缺少版本或完整性 metadata")
    if PurePosixPath(name).suffix.lower() not in MEDIA_EXTENSIONS:
        raise DriveApiError("來源檔案格式不在支援的媒體清單")
    return {
        "id": file_id,
        "name": name,
        "mime_type": str(item.get("mimeType") or "application/octet-stream"),
        "size_bytes": size,
        "version": version,
        "md5_checksum": md5,
        "parents": tuple(sorted(str(parent) for parent in parents)),
        "modified_time": str(item.get("modifiedTime") or ""),
    }


def download_drive_media(
    source_path: str, destination: Path, *, max_bytes: int | None = None,
) -> dict[str, Any]:
    """Stream an exact Drive media path and verify its metadata before/after.

    The destination is create-once. This function performs Drive GET requests
    only; it never writes to Drive or calls an ASR/correction provider.
    """
    limit = max_bytes
    if limit is None:
        try:
            limit = int(os.environ.get("COURSE_TRANSCRIPT_V2_SOURCE_MAX_BYTES", str(2 * 1024**3)))
        except ValueError as exc:
            raise DriveApiError("Golden V2 音訊下載上限設定不正確") from exc
    if type(limit) is not int or limit <= 0:
        raise DriveApiError("Golden V2 音訊下載上限設定不正確")

    before = _drive_media_metadata(source_path)
    if before["size_bytes"] > limit:
        raise DriveApiError("Google Drive 音訊檔案超過單檔下載上限")
    target = Path(destination)
    if target.is_symlink() or target.exists():
        raise DriveApiError("本機暫存目的地必須是新的普通檔案路徑")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    total = 0
    try:
        request = urllib.request.Request(
            f"{_DRIVE_API}/files/{urllib.parse.quote(before['id'], safe='')}?alt=media",
            headers={"Authorization": f"Bearer {_refresh_access_token()}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=60) as response, target.open("xb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit or total > before["size_bytes"]:
                    raise DriveApiError("Google Drive 下載內容超過 metadata 大小")
                digest.update(chunk)
                md5.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except urllib.error.HTTPError as exc:
        target.unlink(missing_ok=True)
        if exc.code == 401:
            reset_drive_api_state()
            raise DriveApiError("Google Drive OAuth 授權已失效，請重新授權") from exc
        if exc.code == 403:
            raise DriveApiError("Google Drive 音訊讀取權限不足或配額受限") from exc
        if exc.code == 404:
            raise DriveApiError("Google Drive 音訊檔案不存在或無權存取") from exc
        raise DriveApiError(f"Google Drive 音訊下載錯誤 ({exc.code})") from exc
    except (OSError, TimeoutError) as exc:
        target.unlink(missing_ok=True)
        raise DriveApiError("Google Drive 音訊下載失敗") from exc
    except DriveApiError:
        target.unlink(missing_ok=True)
        raise

    try:
        after = _validate_drive_media_metadata(_api_json(
            f"/files/{urllib.parse.quote(before['id'], safe='')}",
            {"fields": _DRIVE_METADATA_FIELDS, "supportsAllDrives": "true"},
        ))
    except DriveApiError:
        target.unlink(missing_ok=True)
        raise
    if before != after or total != before["size_bytes"] or md5.hexdigest() != before["md5_checksum"]:
        target.unlink(missing_ok=True)
        raise DriveApiError("Google Drive 音訊版本或下載校驗值在下載前後不一致")
    return {
        "provider": "google_drive",
        "file_id": before["id"],
        "version": before["version"],
        "size_bytes": total,
        "downloaded_sha256": digest.hexdigest(),
        "downloaded_md5": md5.hexdigest(),
        "source_path": _normalize_path(source_path),
        "source_name": before["name"],
        "mime_type": before["mime_type"],
        "modified_time": before["modified_time"],
    }


def _parent_path(path: str) -> str | None:
    normalized = _normalize_path(path)
    if normalized == _root_path():
        return None
    parent = str(PurePosixPath(normalized[len(_root_path()):].strip("/")).parent)
    return _root_path() if parent in {"", "."} else f"{_root_path()}{parent}"


def _entry_from_file(parent_path: str, item: dict[str, Any]) -> DriveEntry | None:
    name = str(item.get("name") or "").strip()
    file_id = str(item.get("id") or "").strip()
    if not name or not file_id:
        return None
    mime_type = str(item.get("mimeType") or "") or None
    is_dir = mime_type == _FOLDER_MIME
    source_path = f"{parent_path.rstrip('/')}/{name}" if parent_path != _root_path() else f"{_root_path()}{name}"
    _remember(source_path, file_id)
    return DriveEntry(
        source_path=source_path,
        name=name,
        is_dir=is_dir,
        size_bytes=0 if is_dir else max(0, int(item.get("size") or 0)),
        modified_at=str(item.get("modifiedTime")) if item.get("modifiedTime") else None,
        mime_type=mime_type,
        supported_media=(not is_dir and PurePosixPath(name).suffix.lower() in MEDIA_EXTENSIONS),
    )


def _search_entry(item: dict[str, Any]) -> DriveEntry | None:
    parents = item.get("parents")
    parent_path = _root_path()
    if isinstance(parents, list) and parents:
        parent_path = _folder_path_by_id(str(parents[0]))
    return _entry_from_file(parent_path, item)


def list_drive_directory(source_path: str, *, page_token: str | None = None, page_size: int | None = None) -> DriveApiPage:
    current_path = _normalize_path(source_path)
    folder_id = _resolve_path_id(current_path)
    size = max(1, min(page_size or int(os.environ.get("GOOGLE_DRIVE_PAGE_SIZE", "200")), 1000))
    params: dict[str, str | int | bool] = {
        "q": f"'{_escape_query(folder_id)}' in parents and trashed = false",
        "pageSize": size,
        "orderBy": "folder,name_natural",
        "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,parents,driveId)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    if page_token:
        params["pageToken"] = page_token
    payload = _api_json("/files", params)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise DriveApiError("Google Drive API 未回傳檔案清單")
    entries = [entry for item in raw_files if isinstance(item, dict) if (entry := _entry_from_file(current_path, item))]
    entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
    return DriveApiPage(current_path, _parent_path(current_path), entries, str(payload.get("nextPageToken") or "") or None)


def search_drive(query_text: str, *, page_token: str | None = None, page_size: int | None = None) -> DriveApiPage:
    text = query_text.strip()
    if len(text) < 2:
        raise DriveApiError("Drive 搜尋至少輸入 2 個字元")
    size = max(1, min(page_size or int(os.environ.get("GOOGLE_DRIVE_PAGE_SIZE", "200")), 1000))
    params: dict[str, str | int | bool] = {
        "q": f"name contains '{_escape_query(text)}' and trashed = false",
        "pageSize": size,
        "orderBy": "folder,name_natural",
        "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,parents,driveId)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    if page_token:
        params["pageToken"] = page_token
    payload = _api_json("/files", params)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise DriveApiError("Google Drive API 未回傳搜尋結果")
    entries = [entry for item in raw_files if isinstance(item, dict) if (entry := _search_entry(item))]
    entries.sort(key=lambda entry: (not entry.is_dir, entry.source_path.casefold()))
    return DriveApiPage(_root_path(), None, entries, str(payload.get("nextPageToken") or "") or None)


def drive_health() -> dict[str, Any]:
    payload = _api_json("/about", {"fields": "user(displayName),storageQuota(limit,usage)"})
    return {
        "status": "ok",
        "provider": "google_api",
        "account_available": isinstance(payload.get("user"), dict),
        "storage_quota_available": isinstance(payload.get("storageQuota"), dict),
        "fallback_available": os.environ.get("COURSE_TRANSCRIPT_DRIVE_BROWSER_FALLBACK", "true").lower() in {"1", "true", "yes"},
        "paid_operation_started": False,
    }
