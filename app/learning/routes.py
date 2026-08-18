"""Reviewer-authenticated learning APIs.

These routes are intentionally under ``/api/v1/review`` so the reviewer-host
boundary can expose them without widening access to owner/admin APIs.
"""
from __future__ import annotations

import json
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
    """Client score/total are accepted for compatibility but never trusted."""

    model_config = ConfigDict(extra="forbid")
    score: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, gt=0)
    artifact_id: str = Field(min_length=1, max_length=128)
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
    return HTTPException(status_code=500, detail="學習功能暫時無法完成，請稍後再試")


def _artifact_quiz(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    content = artifact.get("content")
    if not isinstance(content, dict):
        return []
    raw = content.get("quiz")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _artifact_flashcard_keys(artifact: dict[str, Any]) -> set[str]:
    content = artifact.get("content")
    if not isinstance(content, dict):
        return set()
    raw = content.get("flashcards")
    if not isinstance(raw, list):
        return set()
    keys: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("front") or "").strip()
        if key:
            keys.add(key)
    return keys


def _artifact_search_matches(artifact: dict[str, Any], query: str, limit: int) -> list[dict[str, Any]]:
    """Return human-readable matches whose timestamps come from that exact item."""

    if limit <= 0 or bool(artifact.get("is_stale")):
        return []
    content = artifact.get("content")
    citations = artifact.get("citations")
    if not isinstance(content, dict) or not isinstance(citations, list):
        return []
    citation_map = {
        int(item["segment_index"]): item
        for item in citations
        if isinstance(item, dict) and str(item.get("segment_index", "")).isdigit()
    }
    needle = query.casefold()
    sections: list[tuple[str, list[dict[str, Any]]]] = []
    overview = content.get("overview")
    if isinstance(overview, dict):
        sections.append(("overview", [overview]))
    for key in (
        "detailed_notes",
        "quick_review_10m",
        "quick_review_3m",
        "key_points",
        "qa",
        "flashcards",
        "quiz",
        "glossary",
    ):
        value = content.get(key)
        if isinstance(value, list):
            sections.append((key, [item for item in value if isinstance(item, dict)]))

    results: list[dict[str, Any]] = []
    for section, items in sections:
        for index, item in enumerate(items):
            human_parts: list[str] = []
            for field in (
                "title", "summary", "heading", "text", "question", "answer",
                "front", "back", "term", "explanation",
            ):
                value = item.get(field)
                if isinstance(value, str) and value.strip():
                    human_parts.append(value.strip())
            for field in ("points", "choices"):
                value = item.get(field)
                if isinstance(value, list):
                    human_parts.extend(str(part).strip() for part in value if str(part).strip())
            searchable = " ｜ ".join(human_parts)
            if not searchable or needle not in searchable.casefold():
                continue
            raw_indexes = item.get("source_segment_indexes")
            source_indexes: list[int] = []
            if isinstance(raw_indexes, list):
                for raw_index in raw_indexes:
                    try:
                        source_indexes.append(int(raw_index))
                    except (TypeError, ValueError):
                        continue
            source = next((citation_map[value] for value in source_indexes if value in citation_map), None)
            if source is None:
                continue
            snippet = searchable
            if len(snippet) > 320:
                position = snippet.casefold().find(needle)
                start = max(0, position - 110) if position >= 0 else 0
                snippet = snippet[start:start + 320]
                if start > 0:
                    snippet = f"…{snippet}"
                if start + 320 < len(searchable):
                    snippet = f"{snippet}…"
            results.append(
                {
                    "id": f"{artifact.get('id')}:{section}:{index}",
                    "artifact_id": artifact.get("id"),
                    "youtube_video_id": artifact.get("youtube_video_id"),
                    "video_title": artifact.get("video_title"),
                    "title": artifact.get("title"),
                    "artifact_type": artifact.get("artifact_type"),
                    "section": section,
                    "snippet": snippet,
                    "start_ms": int(source.get("start_ms") or 0),
                    "end_ms": int(source.get("end_ms") or 0),
                    "source_segment_index": int(source.get("segment_index") or 0),
                    "generated_at": artifact.get("generated_at"),
                }
            )
            if len(results) >= limit:
                return results
    return results


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
        # and subtitle-review surfaces resume from one canonical position. Review
        # completion is monotonic in ReviewStore.update_progress and is not reset.
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
    """Grade on the server; client-provided score/total are display hints only."""

    try:
        store = _store()
        artifact = store.artifact_for_video(youtube_video_id)
        if artifact is None or str(artifact["id"]) != payload.artifact_id:
            raise ReviewConflict("這份測驗不是目前課程的最新 AI 學習內容")
        if bool(artifact.get("is_stale")):
            raise ReviewConflict("這份測驗所依據的字幕已更新，請等待管理員重新產生學習內容")
        quiz = _artifact_quiz(artifact)
        if not quiz:
            raise ReviewConflict("這堂課目前沒有可作答的自我測驗")
        score = 0
        graded_total = 0
        for index, question in enumerate(quiz):
            choices = question.get("choices")
            try:
                answer_index = int(question.get("answer_index"))
            except (TypeError, ValueError):
                continue
            if not isinstance(choices, list) or len(choices) < 2 or answer_index < 0 or answer_index >= len(choices):
                continue
            key = str(question.get("id") or question.get("question") or index).strip()
            if not key:
                continue
            graded_total += 1
            try:
                selected = int(payload.answers.get(key))
            except (TypeError, ValueError):
                selected = -1
            if selected == answer_index:
                score += 1
        if graded_total <= 0:
            raise ReviewConflict("這堂課的測驗內容目前無法計分")
        attempt = store.record_quiz_attempt(
            user_id=_user_id(request, mutation=True),
            youtube_video_id=youtube_video_id,
            score=score,
            total=graded_total,
            artifact_id=payload.artifact_id,
            answers=payload.answers,
        )
        return {"attempt": attempt, "score": score, "total": graded_total}
    except Exception as exc:
        raise _raise(exc) from exc


@router.post("/flashcards/review")
def flashcard_review(payload: FlashcardReviewRequest, request: Request) -> dict[str, Any]:
    try:
        store = _store()
        with store.connect() as connection:
            row = connection.execute(
                "SELECT youtube_video_id FROM learning_artifacts WHERE id = ?",
                (payload.artifact_id,),
            ).fetchone()
        if row is None:
            raise ReviewNotFound("找不到這份 Flashcards")
        artifact = store.artifact_for_video(str(row["youtube_video_id"]))
        if artifact is None or str(artifact["id"]) != payload.artifact_id:
            raise ReviewConflict("這組 Flashcards 已不是目前最新的學習內容")
        if bool(artifact.get("is_stale")):
            raise ReviewConflict("這組 Flashcards 所依據的字幕已更新，請等待管理員重新產生學習內容")
        if payload.card_key not in _artifact_flashcard_keys(artifact):
            raise ReviewConflict("找不到這張 Flashcard，請重新整理課程頁面")
        progress = store.review_flashcard(
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
        # The store query supplies current subtitle matches. AI search is rebuilt
        # here from only the current, non-stale artifact so each result can link
        # to the exact citation belonging to the matched Study Pack item.
        remaining = min(20, limit)
        artifact_matches: list[dict[str, Any]] = []
        with store.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT youtube_video_id
                FROM learning_artifacts
                WHERE artifact_type = 'study_pack'
                ORDER BY generated_at DESC
                """
            ).fetchall()
        for row in rows:
            artifact = store.artifact_for_video(str(row["youtube_video_id"]))
            if not artifact or bool(artifact.get("is_stale")):
                continue
            matches = _artifact_search_matches(artifact, q.strip(), remaining)
            artifact_matches.extend(matches)
            remaining -= len(matches)
            if remaining <= 0:
                break
        payload["artifact_results"] = artifact_matches
        return payload
    except Exception as exc:
        raise _raise(exc) from exc
