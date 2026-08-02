"""Google Drive API browsing routes with a read-only rclone fallback."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor
from app.jobs.drive_api import DriveApiError, drive_health, list_drive_directory, search_drive
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


def _provider() -> str:
    return os.environ.get("COURSE_TRANSCRIPT_DRIVE_BROWSER_PROVIDER", "google_api").strip().lower()


def _fallback_enabled() -> bool:
    return os.environ.get("COURSE_TRANSCRIPT_DRIVE_BROWSER_FALLBACK", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def _directory_payload(page: Any) -> dict[str, Any]:
    entries = page.entries
    return {
        "current_path": page.current_path,
        "parent_path": page.parent_path,
        "entries": [entry.to_dict() for entry in entries],
        "next_page_token": page.next_page_token,
        "provider": page.provider,
        "supported_extensions": sorted(
            {
                os.path.splitext(entry.name)[1].lower()
                for entry in entries
                if entry.supported_media
            }
        ),
        "paid_operation_started": False,
    }


@router.post("/api/v1/drive/browse")
def browse_drive(payload: DriveBrowseRequest, request: Request) -> dict[str, Any]:
    _mutation_actor(request)
    provider = _provider()
    if provider == "rclone":
        try:
            current_path, entries = list_rclone_directory(payload.source_path)
        except SourceInspectionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "current_path": current_path,
            "parent_path": None,
            "entries": [entry.to_dict() for entry in entries],
            "next_page_token": None,
            "provider": "rclone",
            "supported_extensions": sorted(
                {
                    os.path.splitext(entry.name)[1].lower()
                    for entry in entries
                    if entry.supported_media
                }
            ),
            "paid_operation_started": False,
        }

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
        return {
            "current_path": current_path,
            "parent_path": None,
            "entries": [entry.to_dict() for entry in entries],
            "next_page_token": None,
            "provider": "rclone_fallback",
            "warning": str(exc),
            "supported_extensions": sorted(
                {
                    os.path.splitext(entry.name)[1].lower()
                    for entry in entries
                    if entry.supported_media
                }
            ),
            "paid_operation_started": False,
        }


@router.post("/api/v1/drive/search")
def search(payload: DriveSearchRequest, request: Request) -> dict[str, Any]:
    _mutation_actor(request)
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
    _mutation_actor(request)
    if _provider() == "rclone":
        return {
            "status": "degraded",
            "provider": "rclone",
            "warning": "Google Drive API browser 尚未啟用",
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
