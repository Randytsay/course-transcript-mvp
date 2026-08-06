"""Google Drive API browsing routes with a read-only rclone fallback."""
from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.jobs.drive_api import (
    DriveApiError,
    clear_drive_path_cache,
    drive_health,
    list_drive_directory,
    search_drive,
)
from app.jobs.source import SourceInspectionError, list_rclone_directory

router = APIRouter()


class DriveBrowseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(default="gdrive:", min_length=4, max_length=2048)
    page_token: str | None = Field(default=None, max_length=2048)
    page_size: int = Field(default=200, ge=1, le=1000)
    refresh: bool = False


class DriveSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=200)
    page_token: str | None = Field(default=None, max_length=2048)
    page_size: int = Field(default=100, ge=1, le=1000)


def _read_actor(request: Request) -> str:
    """Require Cloudflare identity for reads without requiring an Origin header."""
    require_access = os.environ.get(
        "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS", "false"
    ).lower() in {"1", "true", "yes"}
    actor = request.headers.get("Cf-Access-Authenticated-User-Email")
    assertion = request.headers.get("Cf-Access-Jwt-Assertion")
    if require_access and (not actor or not assertion):
        raise HTTPException(status_code=401, detail="Cloudflare Access identity required")
    return actor or "local-development"


def _provider() -> str:
    provider = os.environ.get(
        "COURSE_TRANSCRIPT_DRIVE_BROWSER_PROVIDER", "google_api"
    ).strip().lower()
    if provider not in {"google_api", "rclone"}:
        raise HTTPException(status_code=500, detail="Drive browser provider 設定不正確")
    return provider


def _fallback_enabled() -> bool:
    return os.environ.get("COURSE_TRANSCRIPT_DRIVE_BROWSER_FALLBACK", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def _rclone_parent(path: str) -> str | None:
    root = os.environ.get("COURSE_TRANSCRIPT_ALLOWED_SOURCE_PREFIX", "gdrive:").rstrip("/")
    normalized = path.strip().rstrip("/")
    if normalized == root:
        return None
    if not normalized.startswith(root):
        return None
    relative = normalized[len(root):].strip("/")
    parent = str(PurePosixPath(relative).parent)
    return root if parent in {"", "."} else f"{root}{parent}"


def _entries_payload(
    *,
    current_path: str,
    parent_path: str | None,
    entries: list[Any],
    provider: str,
    next_page_token: str | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "current_path": current_path,
        "parent_path": parent_path,
        "entries": [entry.to_dict() for entry in entries],
        "next_page_token": next_page_token,
        "provider": provider,
        "supported_extensions": sorted(
            {
                os.path.splitext(entry.name)[1].lower()
                for entry in entries
                if entry.supported_media
            }
        ),
        "paid_operation_started": False,
    }
    if warning:
        result["warning"] = warning
    return result


def _directory_payload(page: Any) -> dict[str, Any]:
    return _entries_payload(
        current_path=page.current_path,
        parent_path=page.parent_path,
        entries=page.entries,
        provider=page.provider,
        next_page_token=page.next_page_token,
    )


@router.post("/api/v1/drive/browse")
def browse_drive(payload: DriveBrowseRequest, request: Request) -> dict[str, Any]:
    _read_actor(request)
    if payload.refresh:
        clear_drive_path_cache()
    provider = _provider()
    if provider == "rclone":
        try:
            current_path, entries = list_rclone_directory(payload.source_path)
        except SourceInspectionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _entries_payload(
            current_path=current_path,
            parent_path=_rclone_parent(current_path),
            entries=entries,
            provider="rclone",
        )

    try:
        return _directory_payload(
            list_drive_directory(
                payload.source_path,
                page_token=payload.page_token,
                page_size=payload.page_size,
            )
        )
    except DriveApiError as exc:
        if not _fallback_enabled() or payload.page_token:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            current_path, entries = list_rclone_directory(payload.source_path)
        except SourceInspectionError:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _entries_payload(
            current_path=current_path,
            parent_path=_rclone_parent(current_path),
            entries=entries,
            provider="rclone_fallback",
            warning=str(exc),
        )


@router.post("/api/v1/drive/search")
def search(payload: DriveSearchRequest, request: Request) -> dict[str, Any]:
    _read_actor(request)
    if _provider() == "rclone":
        raise HTTPException(status_code=409, detail="Drive 搜尋需要啟用 Google Drive API browser")
    try:
        return _directory_payload(
            search_drive(
                payload.query,
                page_token=payload.page_token,
                page_size=payload.page_size,
            )
        )
    except DriveApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/v1/drive/health")
def health(request: Request) -> dict[str, Any]:
    _read_actor(request)
    if _provider() == "rclone":
        return {
            "status": "degraded",
            "provider": "rclone",
            "warning": "Google Drive API browser 尚未啟用",
            "fallback_available": True,
            "paid_operation_started": False,
        }
    try:
        return drive_health()
    except DriveApiError as exc:
        return {
            "status": "error",
            "provider": "google_api",
            "warning": str(exc),
            "fallback_available": _fallback_enabled(),
            "paid_operation_started": False,
        }
