"""Reviewer-authenticated learning APIs.

These routes are intentionally under ``/api/v1/review`` so the reviewer-host
boundary can expose them without widening access to owner/admin APIs.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.review.auth import require_reviewer_session
from app.review.store import ReviewConflict, ReviewNotFound

from .store import LearningStore

router = APIRouter(prefix="/api/v1/review/learning", tags=["learning"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
_store_cache: tuple[Path, LearningStore] | None = None


class LearningStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    learning_status: Literal["not_started", "in_progress", "completed"] | None = None
    saved: bool | None = None


class WatchProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    last_playback_ms: int = Field(ge=0)


class BookmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_ms: int = Field(ge=0)
    segment_id: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=20000)
    title: str | None = Field(default=None, max_length=200)
    start_ms: int | None = Field(default=None, ge=0)
    segment_id: int | None = Field(default=None, ge=1)


class QuizAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=0)
    total: int = Field(gt=0)
    artifact_id: str | None = Field(default=None, max_length=128)
    answers: dict[str, Any] = Field(default_factory=dict)


class FlashcardReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=1, max_length=128)
    card_key: str = Field(min_length=1, max_length=200)
    rating: Literal["again", "hard", "good", "easy"]


def _store() -> LearningStore:
    global _store_cache
    path = DATA_DIR / "course-transcript.db"
    if _store_cache is None or _store_cache[0] != path:
        _store_cache = (path, LearningStore(path))
    return _store_cache[1]


def _user_id(request: Request, *, mutation: bool = False) -> str:
    session = require_reviewer_session(request, mutation=mutation)
    return str(session["user_id"])


def _raise(exc: Exception) -> HTTPException:
    # Authentication/origin/CSRF failures are already deliberately classified by
    # the reviewer-auth boundary. Never turn those 401/403 responses into a 500.
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ReviewNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReviewConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Learning operation failed")


@router.get("/dashboard")
def dashboard(request: Request) -> dict[str, Any]:
    payload = _store().dashboard(user_id=_user_id(request))
    # A completed lesson may still retain a playback timestamp for intentional
    # rewatching. Do not present it as the primary "continue learning" card.
    resume = payload.get("continue_learning")
    if isinstance(resume, dict) and resume.get("learning_status") == "completed":
        payload["continue_learning"] = None
    return payload


@router.get("/videos/{youtube_video_id}")
def lesson(youtube_video_id: str, request: Request) -> dict[str, Any]:
    try:
        return _store().lesson(user_id=_user_id(request), youtube_video_id=youtube_video_id)
    except Exception as exc:
        raise _raise(exc) from exc


@router.post("/videos/{youtube_video_id}/state")
def update_state(
    youtube_video_id: str,
    payload: LearningStateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        state = _store().upsert_learning_state(
            user_id=_user_id(request, mutation=True),
            youtube_video_id=youtube_video_id,
            learning_status=payload.learning_status,
            saved=payload.saved,
        )
        return {"learning_state": state}
    except Exception as exc:
        raise _raise(exc) from exc


@router.post("/videos/{youtube_video_id}/watch")
def save_watch_progress(
    youtube_video_id: str,
    payload: WatchProgressRequest,
    request: Request,
) -> dict[str, Any]:
    user_id = _user_id(request, mutation=True)
    store = _store()
    try:
        # Playback state remains in the existing review progress row so learning
        # and subtitle-review surfaces resume from one canonical position.
        progress = store.review_admin.review.update_progress(
            user_id=user_id,
            youtube_video_id=youtube_video_id,
            last_playback_ms=payload.last_playback_ms,
            completed=False,
        )
        with store.connect() as connection:
            current = connection.execute(
                """
                SELECT learning_status FROM learning_video_state
                WHERE user_id = ? AND youtube_video_id = ?
                """,
                (user_id, youtube_video_id),
            ).fetchone()
        # Rewatching an explicitly completed lesson must not silently reopen it.
        if payload.last_playback_ms > 0 and (
            current is None or str(current["learning_status"]) != "completed"
        ):
            store.upsert_learning_state(
                user_id=user_id,
                youtube_video_id=youtube_video_id,
                learning_status="in_progress",
            )
        return {"progress": progress}
    except Exception as exc:
        raise _raise(exc) from exc


@router.get("/notes")
def notes(
    request: Request,
    youtube_video_id: str | None = Query(default=None),
) -> dict[str, Any]:
    return {
        "notes": _store().list_notes(
            user_id=_user_id(request), youtube_video_id=youtube_video_id
        )
    }


@router.post("/videos/{youtube_video_id}/notes")
def create_note(
    youtube_video_id: str,
    payload: NoteRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        note = _store().create_note(
            user_id=_user_id(request, mutation=True),
            youtube_video_id=youtube_video_id,
            body=payload.body,
            title=payload.title,
            start_ms=payload.start_ms,
            segment_id=payload.segment_id,
        )
        return {"note": note}
    except Exception as exc:
        raise _raise(exc) from exc


@router.put("/notes/{note_id}")
def update_note(note_id: str, payload: NoteRequest, request: Request) -> dict[str, Any]:
    try:
        note = _store().update_note(
            user_id=_user_id(request, mutation=True),
            note_id=note_id,
            body=payload.body,
            title=payload.title,
        )
        return {"note": note}
    except Exception as exc:
        raise _raise(exc) from exc


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, request: Request) -> dict[str, bool]:
    try:
        return {
            "deleted": _store().delete_note(
                user_id=_user_id(request, mutation=True), note_id=note_id
            )
        }
    except Exception as exc:
        raise _raise(exc) from exc


@router.get("/bookmarks")
def bookmarks(
    request: Request,
    youtube_video_id: str | None = Query(default=None),
) -> dict[str, Any]:
    return {
        "bookmarks": _store().list_bookmarks(
            user_id=_user_id(request), youtube_video_id=youtube_video_id
        )
    }


@router.post("/videos/{youtube_video_id}/bookmarks")
def create_bookmark(
    youtube_video_id: str,
    payload: BookmarkRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        bookmark = _store().create_bookmark(
            user_id=_user_id(request, mutation=True),
            youtube_video_id=youtube_video_id,
            start_ms=payload.start_ms,
            segment_id=payload.segment_id,
            label=payload.label,
            note=payload.note,
        )
        return {"bookmark": bookmark}
    except Exception as exc:
        raise _raise(exc) from exc


@router.delete("/bookmarks/{bookmark_id}")
def delete_bookmark(bookmark_id: str, request: Request) -> dict[str, bool]:
    try:
        return {
            "deleted": _store().delete_bookmark(
                user_id=_user_id(request, mutation=True), bookmark_id=bookmark_id
            )
        }
    except Exception as exc:
        raise _raise(exc) from exc


@router.get("/review-queue")
def review_queue(request: Request) -> dict[str, Any]:
    return {"items": _store().review_queue(user_id=_user_id(request))}


@router.post("/videos/{youtube_video_id}/review-complete")
def review_complete(youtube_video_id: str, request: Request) -> dict[str, Any]:
    try:
        return {
            "schedule": _store().review_lesson(
                user_id=_user_id(request, mutation=True),
                youtube_video_id=youtube_video_id,
            )
        }
    except Exception as exc:
        raise _raise(exc) from exc


@router.post("/videos/{youtube_video_id}/quiz-attempts")
def quiz_attempt(
    youtube_video_id: str,
    payload: QuizAttemptRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        store = _store()
        if payload.artifact_id:
            artifact = store.artifact_for_video(youtube_video_id)
            if artifact is None or str(artifact["id"]) != payload.artifact_id:
                raise ReviewConflict("Quiz artifact does not belong to this lesson")
        attempt = store.record_quiz_attempt(
            user_id=_user_id(request, mutation=True),
            youtube_video_id=youtube_video_id,
            score=payload.score,
            total=payload.total,
            artifact_id=payload.artifact_id,
            answers=payload.answers,
        )
        return {"attempt": attempt}
    except Exception as exc:
        raise _raise(exc) from exc


@router.post("/flashcards/review")
def flashcard_review(payload: FlashcardReviewRequest, request: Request) -> dict[str, Any]:
    try:
        progress = _store().review_flashcard(
            user_id=_user_id(request, mutation=True),
            artifact_id=payload.artifact_id,
            card_key=payload.card_key,
            rating=payload.rating,
        )
        return {"progress": progress}
    except Exception as exc:
        raise _raise(exc) from exc


@router.get("/search")
def search(
    request: Request,
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=40, ge=1, le=100),
) -> dict[str, Any]:
    try:
        store = _store()
        payload = store.search(user_id=_user_id(request), query=q, limit=limit)
        # Search should not surface superseded/stale AI material as current
        # knowledge. Subtitle results stay available independently.
        current_artifacts: dict[str, dict[str, Any] | None] = {}
        filtered: list[dict[str, Any]] = []
        for item in payload.get("artifact_results", []):
            video_id = str(item.get("youtube_video_id") or "")
            if video_id not in current_artifacts:
                current_artifacts[video_id] = store.artifact_for_video(video_id)
            current = current_artifacts[video_id]
            if (
                current
                and not bool(current.get("is_stale"))
                and str(current.get("id")) == str(item.get("id"))
            ):
                filtered.append(item)
        payload["artifact_results"] = filtered
        return payload
    except Exception as exc:
        raise _raise(exc) from exc
