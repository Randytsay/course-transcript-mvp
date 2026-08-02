"""Read-only Google Drive API browser with rclone-compatible paths.

The browser uses OAuth refresh credentials in memory and never writes the
mounted rclone configuration. rclone remains responsible for bulk transfers.
"""
from __future__ import annotations

import configparser
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.jobs.source import DriveEntry, MEDIA_EXTENSIONS, SourceInspectionError

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_TOKEN_LOCK = threading.Lock()


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
_CACHE_LOCK = threading.Lock()


def _remote_name() -> str:
    return os.environ.get("COURSE_TRANSCRIPT_DRIVE_REMOTE", "gdrive").strip() or "gdrive"


def _root_path() -> str:
    return f"{_remote_name()}:"


def _config_path() -> str:
    return os.environ.get("RCLONE_CONFIG", "/run/secrets/rclone.conf")


def _read_refresh_token_file(path: str) -> str:
    try:
        raw = open(path, "r", encoding="utf-8").read().strip()
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
        raise DriveApiError(
            "Google Drive API 需要自訂 OAuth client_id、client_secret 與 refresh_token"
        )
    return _OAuthState(client_id, client_secret, refresh_token)


def _oauth_state() -> _OAuthState:
    global _OAUTH_STATE
    with _TOKEN_LOCK:
        if _OAUTH_STATE is None:
            _OAUTH_STATE = _load_oauth_state()
        return _OAUTH_STATE


def reset_drive_api_state() -> None:
    """Reset token/path caches for tests and controlled credential rotation."""
    global _OAUTH_STATE
    with _TOKEN_LOCK:
        _OAUTH_STATE = None
    with _CACHE_LOCK:
        _PATH_ID_CACHE.clear()
        _PATH_ID_CACHE[_root_path()] = "root"
        _ID_PATH_CACHE.clear()
        _ID_PATH_CACHE["root"] = _root_path()


def _refresh_access_token() -> str:
    state = _oauth_state()
    now = time.time()
    with _TOKEN_LOCK:
        if state.access_token and state.expires_at - 60 > now:
            return state.access_token
        body = urllib.parse.urlencode(
            {
                "client_id": state.client_id,
                "client_secret": state.client_secret,
                "refresh_token": state.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
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
            raise DriveApiError("Google Drive 資料夾不存在或無權存取") from exc
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
    if value == root:
        return root
    if not value.startswith(root):
        raise DriveApiError("來源路徑不在允許的 Google Drive 範圍")
    relative = value[len(root):].strip("/")
    parts = PurePosixPath(relative).parts
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


def _resolve_path_id(source_path: str) -> str:
    normalized = _normalize_path(source_path)
    with _CACHE_LOCK:
        cached = _PATH_ID_CACHE.get(normalized)
    if cached:
        return _root_folder_id() if cached == "root" else cached

    root = _root_path()
    current_id = _root_folder_id()
    current_path = root
    relative = normalized[len(root):].strip("/")
    for part in PurePosixPath(relative).parts:
        query = (
            f"'{_escape_query(current_id)}' in parents and "
            f"name = '{_escape_query(part)}' and "
            f"mimeType = '{_FOLDER_MIME}' and trashed = false"
        )
        payload = _api_json(
            "/files",
            {
                "q": query,
                "pageSize": 2,
                "fields": "files(id,name)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        files = payload.get("files")
        if not isinstance(files, list) or len(files) != 1:
            raise DriveApiError(f"Google Drive 資料夾路徑無法唯一解析：{part}")
        current_id = str(files[0].get("id") or "")
        if not current_id:
            raise DriveApiError("Google Drive 資料夾缺少識別碼")
        current_path = f"{current_path.rstrip('/')}/{part}" if current_path != root else f"{root}{part}"
        with _CACHE_LOCK:
            _PATH_ID_CACHE[current_path] = current_id
            _ID_PATH_CACHE[current_id] = current_path
    return current_id


def _parent_path(path: str) -> str | None:
    normalized = _normalize_path(path)
    root = _root_path()
    if normalized == root:
        return None
    relative = normalized[len(root):].strip("/")
    parent = str(PurePosixPath(relative).parent)
    return root if parent in {"", "."} else f"{root}{parent}"


def _entry_from_file(parent_path: str, item: dict[str, Any]) -> DriveEntry | None:
    name = str(item.get("name") or "").strip()
    file_id = str(item.get("id") or "").strip()
    if not name or not file_id:
        return None
    mime_type = str(item.get("mimeType") or "") or None
    is_dir = mime_type == _FOLDER_MIME
    source_path = f"{parent_path.rstrip('/')}/{name}" if parent_path != _root_path() else f"{_root_path()}{name}"
    if is_dir:
        with _CACHE_LOCK:
            _PATH_ID_CACHE[source_path] = file_id
            _ID_PATH_CACHE[file_id] = source_path
    return DriveEntry(
        source_path=source_path,
        name=name,
        is_dir=is_dir,
        size_bytes=0 if is_dir else max(0, int(item.get("size") or 0)),
        modified_at=str(item.get("modifiedTime")) if item.get("modifiedTime") else None,
        mime_type=mime_type,
        supported_media=(not is_dir and PurePosixPath(name).suffix.lower() in MEDIA_EXTENSIONS),
    )


def list_drive_directory(
    source_path: str,
    *,
    page_token: str | None = None,
    page_size: int | None = None,
) -> DriveApiPage:
    current_path = _normalize_path(source_path)
    folder_id = _resolve_path_id(current_path)
    requested_size = page_size or int(os.environ.get("GOOGLE_DRIVE_PAGE_SIZE", "200"))
    requested_size = max(1, min(requested_size, 1000))
    query = f"'{_escape_query(folder_id)}' in parents and trashed = false"
    params: dict[str, str | int | bool] = {
        "q": query,
        "pageSize": requested_size,
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
    return DriveApiPage(
        current_path=current_path,
        parent_path=_parent_path(current_path),
        entries=entries,
        next_page_token=str(payload.get("nextPageToken") or "") or None,
    )


def search_drive(
    query_text: str,
    *,
    page_token: str | None = None,
    page_size: int | None = None,
) -> DriveApiPage:
    text = query_text.strip()
    if len(text) < 2:
        raise DriveApiError("Drive 搜尋至少輸入 2 個字元")
    requested_size = page_size or int(os.environ.get("GOOGLE_DRIVE_PAGE_SIZE", "200"))
    requested_size = max(1, min(requested_size, 1000))
    query = f"name contains '{_escape_query(text)}' and trashed = false"
    params: dict[str, str | int | bool] = {
        "q": query,
        "pageSize": requested_size,
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
    root = _root_path()
    entries = [entry for item in raw_files if isinstance(item, dict) if (entry := _entry_from_file(root, item))]
    entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
    return DriveApiPage(
        current_path=root,
        parent_path=None,
        entries=entries,
        next_page_token=str(payload.get("nextPageToken") or "") or None,
    )


def drive_health() -> dict[str, Any]:
    payload = _api_json("/about", {"fields": "user(displayName),storageQuota(limit,usage)"})
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    quota = payload.get("storageQuota") if isinstance(payload.get("storageQuota"), dict) else {}
    return {
        "status": "ok",
        "provider": "google_api",
        "account_available": bool(user),
        "storage_quota_available": bool(quota),
        "paid_operation_started": False,
    }
