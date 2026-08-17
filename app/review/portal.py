"""Reviewer portal API: videos, synchronized segments, progress and edit leases."""
from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import require_reviewer_session
from .lease_store import ReviewLeaseError, ReviewLeaseStore
from .store import ReviewConflict, ReviewNotFound, ReviewStore

router = APIRouter(prefix="/api/v1/review", tags=["review-portal"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
_review_store_cache: tuple[Path, ReviewStore] | None = None
_lease_store_cache: tuple[Path, ReviewLeaseStore] | None = None


class ProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    last_playback_ms: int = Field(ge=0)
    reviewed_until_ms: int | None = Field(default=None, ge=0)
    last_segment_index: int | None = Field(default=None, ge=1)
    completed: bool = False


class LeaseTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: str = Field(min_length=20, max_length=256)


class SuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


def _database_path() -> Path:
    return DATA_DIR / "course-transcript.db"


def _review_store() -> ReviewStore:
    global _review_store_cache
    path = _database_path()
    if _review_store_cache is None or _review_store_cache[0] != path:
        _review_store_cache = (path, ReviewStore(path))
    return _review_store_cache[1]


def _lease_store() -> ReviewLeaseStore:
    global _lease_store_cache
    path = _database_path()
    _review_store()
    if _lease_store_cache is None or _lease_store_cache[0] != path:
        limit = int(os.environ.get("REVIEW_MAX_EDITORS_PER_VIDEO", "2"))
        _lease_store_cache = (path, ReviewLeaseStore(path, max_editors_per_video=limit))
    return _lease_store_cache[1]


def _video_row(youtube_video_id: str) -> dict[str, Any]:
    with closing(_review_store().connect()) as connection:
        row = connection.execute(
            "SELECT * FROM review_videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return dict(row)


def _serialize_progress(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


@router.get("/videos")
def list_videos(request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request)
    user_id = str(session["user_id"])
    _lease_store()
    now_expression = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"
    with closing(_review_store().connect()) as connection:
        rows = connection.execute(
            f"""
            SELECT
                v.youtube_video_id,
                v.playlist_id,
                v.title,
                v.duration_ms,
                v.caption_language,
                v.updated_at,
                COUNT(DISTINCT seg.id) AS segment_count,
                COALESCE(p.last_playback_ms, 0) AS last_playback_ms,
                COALESCE(p.reviewed_until_ms, 0) AS reviewed_until_ms,
                COALESCE(p.completed, 0) AS completed,
                COUNT(DISTINCT CASE WHEN l.expires_at > {now_expression} THEN l.user_id END)
                    AS active_editor_count,
                COUNT(DISTINCT CASE WHEN s.user_id = ? THEN s.id END)
                    AS my_suggestion_count
            FROM review_videos v
            LEFT JOIN review_subtitle_segments seg
                ON seg.youtube_video_id = v.youtube_video_id
            LEFT JOIN review_video_progress p
                ON p.youtube_video_id = v.youtube_video_id AND p.user_id = ?
            LEFT JOIN review_edit_leases l
                ON l.youtube_video_id = v.youtube_video_id
            LEFT JOIN review_suggestions s
                ON s.segment_id = seg.id
            GROUP BY v.youtube_video_id
            ORDER BY v.imported_at DESC, v.title
            """,
            (user_id, user_id),
        ).fetchall()
    videos = [dict(row) for row in rows]
    for item in videos:
        item["completed"] = bool(item["completed"])
    return {
        "videos": videos,
        "resume": _review_store().get_resume_point(user_id),
        "max_editors_per_video": _lease_store().max_editors_per_video,
    }


@router.get("/videos/{youtube_video_id}")
def get_video(youtube_video_id: str, request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request)
    user_id = str(session["user_id"])
    video = _video_row(youtube_video_id)
    with closing(_review_store().connect()) as connection:
        progress = connection.execute(
            """
            SELECT * FROM review_video_progress
            WHERE user_id = ? AND youtube_video_id = ?
            """,
            (user_id, youtube_video_id),
        ).fetchone()
        segments = connection.execute(
            """
            SELECT
                seg.id,
                seg.segment_index,
                seg.start_ms,
                seg.end_ms,
                seg.working_text,
                seg.revision,
                s.id AS my_suggestion_id,
                s.suggested_text AS my_suggested_text,
                s.changed_chars AS my_changed_chars,
                s.updated_at AS my_suggestion_updated_at
            FROM review_subtitle_segments seg
            LEFT JOIN review_suggestions s
                ON s.segment_id = seg.id
               AND s.user_id = ?
               AND s.status = 'pending'
            WHERE seg.youtube_video_id = ?
            ORDER BY seg.segment_index
            """,
            (user_id, youtube_video_id),
        ).fetchall()
    return {
        "video": video,
        "segments": [dict(row) for row in segments],
        "progress": _serialize_progress(progress),
        "active_editors": _lease_store().active_editors(youtube_video_id),
        "max_editors": _lease_store().max_editors_per_video,
    }


@router.post("/videos/{youtube_video_id}/lease")
def acquire_lease(youtube_video_id: str, request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request, mutation=True)
    try:
        return _lease_store().acquire(
            user_id=str(session["user_id"]),
            youtube_video_id=youtube_video_id,
        )
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/videos/{youtube_video_id}/lease/heartbeat")
def heartbeat_lease(
    youtube_video_id: str,
    payload: LeaseTokenRequest,
    request: Request,
) -> dict[str, Any]:
    session = require_reviewer_session(request, mutation=True)
    try:
        return _lease_store().heartbeat(
            user_id=str(session["user_id"]),
            youtube_video_id=youtube_video_id,
            lease_token=payload.lease_token,
        )
    except ReviewLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/videos/{youtube_video_id}/lease/release")
def release_lease(
    youtube_video_id: str,
    payload: LeaseTokenRequest,
    request: Request,
) -> dict[str, Any]:
    session = require_reviewer_session(request, mutation=True)
    released = _lease_store().release(
        user_id=str(session["user_id"]),
        youtube_video_id=youtube_video_id,
        lease_token=payload.lease_token,
    )
    return {"released": released}


@router.post("/videos/{youtube_video_id}/progress")
def save_progress(
    youtube_video_id: str,
    payload: ProgressRequest,
    request: Request,
) -> dict[str, Any]:
    session = require_reviewer_session(request, mutation=True)
    _video_row(youtube_video_id)
    try:
        progress = _review_store().update_progress(
            user_id=str(session["user_id"]),
            youtube_video_id=youtube_video_id,
            last_playback_ms=payload.last_playback_ms,
            reviewed_until_ms=payload.reviewed_until_ms,
            last_segment_index=payload.last_segment_index,
            completed=payload.completed,
        )
    except (ValueError, ReviewNotFound) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"progress": progress}


def _segment_for_video(youtube_video_id: str, segment_id: int) -> dict[str, Any]:
    with closing(_review_store().connect()) as connection:
        row = connection.execute(
            """
            SELECT * FROM review_subtitle_segments
            WHERE id = ? AND youtube_video_id = ?
            """,
            (segment_id, youtube_video_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Subtitle segment not found")
    return dict(row)


@router.post("/videos/{youtube_video_id}/segments/{segment_id}/suggestion")
def save_suggestion(
    youtube_video_id: str,
    segment_id: int,
    payload: SuggestionRequest,
    request: Request,
    x_review_lease: str = Header(default="", alias="X-Review-Lease"),
) -> dict[str, Any]:
    session = require_reviewer_session(request, mutation=True)
    user_id = str(session["user_id"])
    _segment_for_video(youtube_video_id, segment_id)
    if not _lease_store().validate(
        user_id=user_id,
        youtube_video_id=youtube_video_id,
        lease_token=x_review_lease,
    ):
        raise HTTPException(status_code=409, detail="Active edit lease required")

    with closing(_review_store().connect()) as connection:
        existing = connection.execute(
            """
            SELECT id FROM review_suggestions
            WHERE segment_id = ? AND user_id = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (segment_id, user_id),
        ).fetchone()
    try:
        if existing is None:
            suggestion = _review_store().submit_suggestion(
                segment_id=segment_id,
                user_id=user_id,
                suggested_text=payload.text,
            )
            created = True
        else:
            suggestion = _review_store().revise_suggestion(
                suggestion_id=str(existing["id"]),
                user_id=user_id,
                suggested_text=payload.text,
            )
            created = False
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ReviewConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"suggestion": suggestion, "created": created}
