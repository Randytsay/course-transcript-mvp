"""Bounded, read-only Drive link discovery; no download, billing or mutation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit

from app.jobs import drive_api as drive
from app.jobs.source import MEDIA_EXTENSIONS
from app.pipeline.golden_v2_reference_companion import (
    REFERENCE_EXTENSIONS, _score, match_sibling_references,
)

_ID = r"[A-Za-z0-9_-]{10,200}"


def parse_drive_link(value: str) -> tuple[str, str | None]:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("請貼上有效的 Google Drive 檔案或資料夾連結")
    try:
        url = urlsplit(value.strip())
        if (url.scheme != "https" or url.hostname != "drive.google.com"
                or url.username or url.password or url.port is not None or url.fragment):
            raise ValueError
        query = parse_qs(url.query, keep_blank_values=True)
        if "resourcekey" in query:
            # Resource keys require a separate authenticated transport contract.
            raise ValueError
        match = re.fullmatch(rf"/drive/(?:u/\d+/)?folders/({_ID})/?", url.path)
        if match:
            return match[1], "folder"
        match = re.fullmatch(rf"/file/d/({_ID})(?:/view|/preview)?/?", url.path)
        if match:
            return match[1], "file"
        if url.path == "/open" and len(query.get("id", [])) == 1 and re.fullmatch(_ID, query["id"][0]):
            return query["id"][0], None
    except (ValueError, TypeError):
        pass
    raise ValueError("只支援一般 Drive 檔案／資料夾連結；資源金鑰連結需另行設定")


def _name(item: dict) -> str:
    name = item.get("name")
    if (not isinstance(name, str) or not name or name != name.strip()
            or name in {".", ".."} or "/" in name or "\\" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise drive.DriveApiError("Drive 名稱無法安全轉為來源路徑")
    return name


def _item(file_id: str) -> dict:
    item = drive._api_json(f"/files/{quote(file_id, safe='')}", {
        "fields": drive._DRIVE_METADATA_FIELDS, "supportsAllDrives": "true",
    })
    if item.get("id") != file_id or item.get("trashed") is True:
        raise drive.DriveApiError("Drive 來源不存在或已移至垃圾桶")
    _name(item)
    return item


def _folder_path(folder_id: str, allowed_root: str, trail: frozenset[str] = frozenset()) -> str:
    # Deliberately never trust the process-wide browser path cache for ancestry.
    if folder_id == allowed_root:
        return drive._root_path()
    if folder_id in trail or len(trail) >= 20:
        raise drive.DriveApiError("Drive 父資料夾循環或超過深度限制")
    item = _item(folder_id)
    parents = item.get("parents")
    if item.get("mimeType") != drive._FOLDER_MIME or not isinstance(parents, list) or len(parents) != 1:
        raise drive.DriveApiError("Drive 來源不在允許的根目錄內")
    parent = _folder_path(str(parents[0]), allowed_root, trail | {folder_id})
    return _join(parent, _name(item))


def _join(parent: str, name: str) -> str:
    return parent + name if parent == drive._root_path() else parent + "/" + name


def preview_drive_link(link: str, *, recursive: bool = False, mp3_only: bool = False,
                       max_files: int = 100,
                       max_bytes: int = 10 * 1024**3, max_depth: int = 5,
                       max_entries: int = 2000, max_pages: int = 100,
                       max_file_bytes: int = 2 * 1024**3) -> dict:
    if (type(recursive) is not bool or type(mp3_only) is not bool
            or any(type(n) is not int or n <= 0 for n in
            (max_files, max_bytes, max_entries, max_pages, max_file_bytes)) or type(max_depth) is not int or max_depth < 0):
        raise ValueError("Invalid discovery limits")
    file_id, kind = parse_drive_link(link)
    drive.clear_drive_path_cache()
    root_id = drive._root_folder_id()
    if root_id == "root":
        root_id = str(drive._api_json("/files/root", {"fields": "id", "supportsAllDrives": "true"}).get("id") or "")
        if not re.fullmatch(_ID, root_id):
            raise drive.DriveApiError("Drive 根目錄識別碼無效")
    target = _item(file_id)
    is_folder = target.get("mimeType") == drive._FOLDER_MIME
    if kind and is_folder != (kind == "folder"):
        raise drive.DriveApiError("連結類型與 Drive 項目不一致")
    if is_folder:
        source_path = _folder_path(file_id, root_id)
        # Reject duplicate-name ancestry and ensure fresh path resolves this ID.
        if drive._resolve_path_id(source_path) not in ({file_id, "root"} if file_id == root_id else {file_id}):
            raise drive.DriveApiError("Drive 資料夾路徑無法唯一解析")
    else:
        parents = target.get("parents")
        if not isinstance(parents, list) or len(parents) != 1:
            raise drive.DriveApiError("Drive 來源不在允許的根目錄內")
        source_path = _join(_folder_path(str(parents[0]), root_id), _name(target))

    files: dict[str, dict] = {}
    media_parents: dict[str, str] = {}
    sibling_references: dict[str, dict] = {}
    skipped = 0
    visited: set[str] = set()
    pages = entries = total = 0

    def add(path: str, expected_id: str, parent_id: str) -> None:
        nonlocal total
        metadata = drive.read_drive_media_metadata(path)
        if metadata["id"] != expected_id:
            raise drive.DriveApiError("Drive 檔案路徑在預覽期間變更")
        previous = files.get(expected_id)
        item = {"file_id": expected_id, "version": metadata["version"],
                "source_path": path, "name": metadata["name"],
                "size_bytes": metadata["size_bytes"], "md5_checksum": metadata["md5_checksum"]}
        if previous:
            if previous != item:
                raise drive.DriveApiError("Drive 重複項目的版本不一致")
            return
        if item["size_bytes"] > max_file_bytes:
            raise drive.DriveApiError("Drive 檔案超過單檔預檢下載上限")
        total += item["size_bytes"]
        if len(files) >= max_files or total > max_bytes:
            raise drive.DriveApiError("Drive 批次超過檔案數或總大小限制；請縮小資料夾範圍")
        files[expected_id] = item
        media_parents[expected_id] = parent_id

    def remember_reference(item: dict, parent_id: str, path: str) -> None:
        name, file_id = _name(item), str(item.get("id") or "")
        if (PurePosixPath(name).suffix.lower() not in REFERENCE_EXTENSIONS
                or str(item.get("mimeType", "")).startswith("application/vnd.google-apps.")):
            return
        sibling_references[file_id] = {"file_id": file_id, "name": name,
                                       "parent_id": parent_id, "source_path": path}

    def visit(folder_id: str, path: str, depth: int) -> None:
        nonlocal skipped, pages, entries
        if folder_id in visited:
            raise drive.DriveApiError("Drive 資料夾重複或循環")
        visited.add(folder_id)
        token = None
        tokens: set[str] = set()
        names: dict[str, str] = {}
        while True:
            pages += 1
            if pages > max_pages:
                raise drive.DriveApiError("Drive 清單超過分頁限制")
            params = {"q": f"'{drive._escape_query(folder_id)}' in parents and trashed = false",
                      "pageSize": 200, "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,parents,trashed)",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            payload = drive._api_json("/files", params)
            raw = payload.get("files")
            if payload.get("incompleteSearch") or not isinstance(raw, list):
                raise drive.DriveApiError("Drive 清單不完整，請重新預覽")
            for item in raw:
                entries += 1
                if entries > max_entries or not isinstance(item, dict):
                    raise drive.DriveApiError("Drive 清單超過項目限制或格式無效")
                name = _name(item)
                child_id = str(item.get("id") or "")
                parents = item.get("parents")
                if not re.fullmatch(_ID, child_id) or not isinstance(parents, list) or folder_id not in parents:
                    raise drive.DriveApiError("Drive 清單來源不一致")
                if name in names and names[name] != child_id:
                    raise drive.DriveApiError("Drive 同一資料夾有重複名稱，無法安全選取")
                names[name] = child_id
                child_path = _join(path, name)
                if item.get("mimeType") == drive._FOLDER_MIME:
                    if recursive:
                        if depth >= max_depth:
                            raise drive.DriveApiError("Drive 子資料夾超過深度限制")
                        visit(child_id, child_path, depth + 1)
                    else:
                        skipped += 1
                elif (not str(item.get("mimeType", "")).startswith("application/vnd.google-apps.")
                      and PurePosixPath(name).suffix.lower() in MEDIA_EXTENSIONS):
                    if mp3_only and PurePosixPath(name).suffix.lower() != ".mp3":
                        skipped += 1
                    else:
                        add(child_path, child_id, folder_id)
                else:
                    remember_reference(item, folder_id, child_path)
                    skipped += 1
            token = payload.get("nextPageToken")
            if not token:
                break
            if not isinstance(token, str) or token in tokens:
                raise drive.DriveApiError("Drive 分頁重複或無效")
            tokens.add(token)

    if is_folder:
        visit(file_id, source_path, 0)
    else:
        if mp3_only and PurePosixPath(source_path).suffix.lower() != ".mp3":
            raise ValueError("MP3-only selection requires an MP3 file or folder")
        # A direct media link still needs bounded GET-only sibling discovery.
        parent_id = str(target['parents'][0])
        parent_path = _folder_path(parent_id, root_id)
        add(source_path, file_id, parent_id)
        token = None
        tokens: set[str] = set()
        while True:
            pages += 1
            if pages > max_pages:
                raise drive.DriveApiError("Drive 參考逐字稿掃描超過分頁限制")
            params = {"q": f"'{drive._escape_query(parent_id)}' in parents and trashed = false",
                      "pageSize": 200, "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,parents,trashed)",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params['pageToken'] = token
            payload = drive._api_json('/files', params)
            children = payload.get('files')
            if payload.get('incompleteSearch') or not isinstance(children, list):
                raise drive.DriveApiError("Drive 參考逐字稿清單不完整")
            for item in children:
                entries += 1
                if entries > max_entries or not isinstance(item, dict):
                    raise drive.DriveApiError("Drive 參考逐字稿清單超過限制")
                name = _name(item)
                if (item.get('parents') != [parent_id]
                        or not re.fullmatch(_ID, str(item.get('id') or ''))):
                    raise drive.DriveApiError("Drive 參考逐字稿父目錄不一致")
                remember_reference(item, parent_id, _join(parent_path, name))
            token = payload.get('nextPageToken')
            if not token:
                break
            if token in tokens or not isinstance(token, str):
                raise drive.DriveApiError("Drive 參考逐字稿分頁無效")
            tokens.add(token)

    # Authenticate candidate metadata only for same-folder plausible matches;
    # a mixed folder must never leak a different lesson's transcript into ASR.
    candidate_ids = {ref['file_id'] for ref in sibling_references.values()
                     if any(media_parents[media_id] == ref['parent_id']
                            and _score(item['name'], ref['name']) > 0
                            for media_id, item in files.items())}
    verified = []
    unusable = set()
    for ref_id in sorted(candidate_ids):
        ref = sibling_references[ref_id]
        try:
            metadata = drive.read_drive_reference_metadata(ref_id, ref['parent_id'])
            if metadata['name'] != ref['name']:
                raise drive.DriveApiError('參考逐字稿名稱在掃描期間變更')
            verified.append({**ref, **metadata})
        except drive.DriveApiError:
            unusable.add(ref_id)
    media_inputs = [{**item, 'parent_id': media_parents[file_id]}
                    for file_id, item in files.items()]
    matched = match_sibling_references(media_inputs, verified)
    for item in media_inputs:
        choice = matched.get(item['file_id'])
        if any(media_parents[item['file_id']] == sibling_references[r]['parent_id']
               and _score(item['name'], sibling_references[r]['name']) > 0
               for r in unusable):
            choice = {'status': 'UNAVAILABLE', 'files': []}
        if choice:
            files[item['file_id']]['references'] = choice
    identity = {"schema": "golden-v2-link-preview-v1", "target_id": file_id,
                "recursive": recursive, "files": sorted(files.values(), key=lambda x: x["file_id"])}
    # Selection is part of the signed preview identity: the submitter cannot
    # switch between all media and MP3-only using the same approval digest.
    if mp3_only:
        identity["media_selection"] = "mp3_only"
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    return {**identity, "preview_sha256": digest, "source_path": source_path,
            "total_bytes": total, "skipped_count": skipped,
            "estimated_cents": None, "estimate_status": "DURATION_PREFLIGHT_REQUIRED",
            "download_started": False, "provider_calls_started": False, "drive_mutation_started": False}
