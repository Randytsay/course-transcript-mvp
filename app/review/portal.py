"""Reviewer portal API: videos, synchronized segments, progress and edit leases."""
from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import UTC, datetime
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


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: bool


class LeaseTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: str = Field(min_length=20, max_length=256)


class SuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


class BatchReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    find_text: str = Field(min_length=1, max_length=200)
    replace_text: str = Field(min_length=1, max_length=4000)


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


def _withdrawn_ids(connection: Any, user_id: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT e.suggestion_id
        FROM review_suggestion_events e
        JOIN review_suggestions s ON s.id = e.suggestion_id
        WHERE s.user_id = ? AND e.event_type = 'withdrawn'
        """,
        (user_id,),
    ).fetchall()
    return {str(row["suggestion_id"]) for row in rows}


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
                COUNT(DISTINCT CASE
                    WHEN s.user_id = ?
                     AND NOT EXISTS (
                        SELECT 1 FROM review_suggestion_events se
                        WHERE se.suggestion_id = s.id AND se.event_type = 'withdrawn'
                     )
                    THEN s.id END
                ) AS my_suggestion_count,
                COUNT(DISTINCT CASE WHEN s.user_id = ? AND s.status = 'approved' THEN s.id END)
                    AS my_approved_count,
                COUNT(DISTINCT CASE WHEN s.user_id = ? AND s.status = 'pending' THEN s.id END)
                    AS my_pending_count
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
            (user_id, user_id, user_id, user_id),
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
                s.updated_at AS my_suggestion_updated_at,
                s.status AS my_suggestion_status,
                s.reviewed_at AS my_suggestion_reviewed_at,
                CASE WHEN EXISTS (
                    SELECT 1 FROM review_suggestion_events se
                    WHERE se.suggestion_id = s.id AND se.event_type = 'withdrawn'
                ) THEN 1 ELSE 0 END AS my_suggestion_withdrawn
            FROM review_subtitle_segments seg
            LEFT JOIN review_suggestions s
                ON s.id = (
                    SELECT s2.id
                    FROM review_suggestions s2
                    WHERE s2.segment_id = seg.id AND s2.user_id = ?
                    ORDER BY s2.updated_at DESC, s2.created_at DESC
                    LIMIT 1
                )
            WHERE seg.youtube_video_id = ?
            ORDER BY seg.segment_index
            """,
            (user_id, youtube_video_id),
        ).fetchall()
        contributors = connection.execute(
            """
            SELECT
                u.id AS user_id,
                u.display_name,
                u.avatar_url,
                COUNT(s.id) AS suggestions_sent,
                SUM(CASE WHEN s.status = 'approved' THEN 1 ELSE 0 END)
                    AS approved_suggestions,
                MAX(s.updated_at) AS last_contributed_at
            FROM review_suggestions s
            JOIN review_subtitle_segments seg ON seg.id = s.segment_id
            JOIN review_users u ON u.id = s.user_id
            WHERE seg.youtube_video_id = ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM review_suggestion_events withdrawn
                  WHERE withdrawn.suggestion_id = s.id
                    AND withdrawn.event_type = 'withdrawn'
              )
            GROUP BY u.id, u.display_name, u.avatar_url
            ORDER BY last_contributed_at DESC, u.display_name
            """,
            (youtube_video_id,),
        ).fetchall()
    segment_rows = [dict(row) for row in segments]
    for item in segment_rows:
        if item.get("my_suggestion_withdrawn"):
            item["my_suggestion_status"] = "withdrawn"
    contributor_rows = [dict(row) for row in contributors]
    for item in contributor_rows:
        item["suggestions_sent"] = int(item["suggestions_sent"] or 0)
        item["approved_suggestions"] = int(item["approved_suggestions"] or 0)
    return {
        "video": video,
        "segments": segment_rows,
        "progress": _serialize_progress(progress),
        "active_editors": _lease_store().active_editors(youtube_video_id),
        "contributors": contributor_rows,
        "max_editors": _lease_store().max_editors_per_video,
    }


@router.get("/suggestions/me")
def my_suggestions(request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request)
    user_id = str(session["user_id"])
    with closing(_review_store().connect()) as connection:
        rows = connection.execute(
            """
            SELECT
                s.id,
                s.segment_id,
                s.original_text_snapshot,
                s.suggested_text,
                s.changed_chars,
                s.status,
                s.created_at,
                s.updated_at,
                s.reviewed_at,
                seg.youtube_video_id,
                seg.segment_index,
                seg.start_ms,
                seg.end_ms,
                seg.working_text AS current_text,
                v.title AS video_title,
                CASE WHEN EXISTS (
                    SELECT 1 FROM review_suggestion_events se
                    WHERE se.suggestion_id = s.id AND se.event_type = 'withdrawn'
                ) THEN 1 ELSE 0 END AS withdrawn
            FROM review_suggestions s
            JOIN review_subtitle_segments seg ON seg.id = s.segment_id
            JOIN review_videos v ON v.youtube_video_id = seg.youtube_video_id
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
            LIMIT 1000
            """,
            (user_id,),
        ).fetchall()
        withdrawn = _withdrawn_ids(connection, user_id)
        rejection_reasons: dict[str, str] = {}
        try:
            audit_rows = connection.execute(
                """
                SELECT entity_id, payload_json
                FROM review_admin_audit
                WHERE entity_type = 'suggestion' AND action = 'suggestion_rejected'
                ORDER BY id DESC
                """
            ).fetchall()
            for audit in audit_rows:
                suggestion_id = str(audit["entity_id"])
                if suggestion_id in rejection_reasons:
                    continue
                try:
                    payload = json.loads(audit["payload_json"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                reason = str(payload.get("reason") or "").strip()
                if reason:
                    rejection_reasons[suggestion_id] = reason
        except Exception:
            # review_admin_audit is created lazily by the owner workflow. A
            # reviewer history read must remain available before the first admin visit.
            rejection_reasons = {}

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        suggestion_id = str(item["id"])
        if suggestion_id in withdrawn or item.get("withdrawn"):
            item["display_status"] = "withdrawn"
        else:
            item["display_status"] = str(item["status"])
        item["review_reason"] = rejection_reasons.get(suggestion_id)
        result.append(item)
    return {"suggestions": result}


@router.post("/suggestions/{suggestion_id}/withdraw")
def withdraw_suggestion(suggestion_id: str, request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request, mutation=True)
    user_id = str(session["user_id"])
    now = datetime.now(UTC).isoformat()
    with _review_store().transaction() as connection:
        suggestion = connection.execute(
            "SELECT * FROM review_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
        if suggestion is None:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        if str(suggestion["user_id"]) != user_id:
            raise HTTPException(status_code=403, detail="Suggestion belongs to another reviewer")
        if str(suggestion["status"]) != "pending":
            raise HTTPException(status_code=409, detail="Only pending suggestions can be withdrawn")
        connection.execute(
            """
            UPDATE review_suggestions
            SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (user_id, now, now, suggestion_id),
        )
        connection.execute(
            """
            INSERT INTO review_suggestion_events(
                suggestion_id, event_type, actor_user_id, payload_json, created_at
            ) VALUES (?, 'withdrawn', ?, '{}', ?)
            """,
            (suggestion_id, user_id, now),
        )
        row = connection.execute(
            "SELECT * FROM review_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
    return {"suggestion": dict(row), "withdrawn": True}


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


@router.post("/videos/{youtube_video_id}/progress/completion")
def set_completion(
    youtube_video_id: str,
    payload: CompletionRequest,
    request: Request,
) -> dict[str, Any]:
    """Apply an explicit reviewer status change without reopening on playback."""
    session = require_reviewer_session(request, mutation=True)
    _video_row(youtube_video_id)
    try:
        progress = _review_store().set_completion(
            user_id=str(session["user_id"]),
            youtube_video_id=youtube_video_id,
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


@router.post("/videos/{youtube_video_id}/batch-suggestion")
def save_batch_suggestions(
    youtube_video_id: str,
    payload: BatchReplaceRequest,
    request: Request,
    x_review_lease: str = Header(default="", alias="X-Review-Lease"),
) -> dict[str, Any]:
    session = require_reviewer_session(request, mutation=True)
    user_id = str(session["user_id"])
    if not _lease_store().validate(
        user_id=user_id,
        youtube_video_id=youtube_video_id,
        lease_token=x_review_lease,
    ):
        raise HTTPException(status_code=409, detail="Active edit lease required")
    try:
        batch = _review_store().submit_batch_replace_suggestions(
            youtube_video_id=youtube_video_id,
            user_id=user_id,
            find_text=payload.find_text,
            replace_text=payload.replace_text,
        )
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ReviewConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"batch": batch}
