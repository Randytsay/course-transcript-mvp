"""Read-only contribution views for the reviewer portal."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from .auth import require_reviewer_session
from .store import ReviewStore

router = APIRouter(prefix="/api/v1/review/contributions", tags=["review-contributions"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
_store_cache: tuple[Path, ReviewStore] | None = None


def _store() -> ReviewStore:
    global _store_cache
    path = DATA_DIR / "course-transcript.db"
    if _store_cache is None or _store_cache[0] != path:
        _store_cache = (path, ReviewStore(path))
    return _store_cache[1]


def _withdrawn_filter(alias: str = "s") -> str:
    return f"""
        NOT EXISTS (
            SELECT 1 FROM review_suggestion_events withdrawn
            WHERE withdrawn.suggestion_id = {alias}.id
              AND withdrawn.event_type = 'withdrawn'
        )
    """


def _leaderboard_rows(limit: int = 100) -> list[dict[str, Any]]:
    with _store().connect() as connection:
        rows = connection.execute(
            f"""
            WITH suggestion_totals AS (
                SELECT
                    s.user_id,
                    COUNT(*) AS suggestions_sent,
                    COALESCE(SUM(s.changed_chars), 0) AS changed_chars,
                    COUNT(DISTINCT seg.youtube_video_id) AS videos_contributed,
                    SUM(CASE WHEN s.status = 'approved' THEN 1 ELSE 0 END) AS approved_suggestions,
                    SUM(CASE WHEN s.status = 'pending' THEN 1 ELSE 0 END) AS pending_suggestions
                FROM review_suggestions s
                JOIN review_subtitle_segments seg ON seg.id = s.segment_id
                WHERE {_withdrawn_filter('s')}
                GROUP BY s.user_id
            ),
            completion_totals AS (
                SELECT
                    user_id,
                    SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS completed_videos
                FROM review_video_progress
                GROUP BY user_id
            )
            SELECT
                u.id AS user_id,
                u.display_name,
                u.avatar_url,
                COALESCE(s.suggestions_sent, 0) AS suggestions_sent,
                COALESCE(s.changed_chars, 0) AS changed_chars,
                COALESCE(s.videos_contributed, 0) AS videos_contributed,
                COALESCE(s.approved_suggestions, 0) AS approved_suggestions,
                COALESCE(s.pending_suggestions, 0) AS pending_suggestions,
                COALESCE(c.completed_videos, 0) AS completed_videos
            FROM review_users u
            LEFT JOIN suggestion_totals s ON s.user_id = u.id
            LEFT JOIN completion_totals c ON c.user_id = u.id
            WHERE u.status = 'active'
            ORDER BY completed_videos DESC, videos_contributed DESC,
                     suggestions_sent DESC, changed_chars DESC, u.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _my_detail(user_id: str) -> dict[str, Any]:
    store = _store()
    user = store.get_user(user_id)
    with store.connect() as connection:
        totals = connection.execute(
            f"""
            SELECT
                COUNT(s.id) AS suggestions_sent,
                COALESCE(SUM(s.changed_chars), 0) AS changed_chars,
                COUNT(DISTINCT seg.youtube_video_id) AS videos_contributed,
                SUM(CASE WHEN s.status = 'approved' THEN 1 ELSE 0 END) AS approved_suggestions,
                SUM(CASE WHEN s.status = 'pending' THEN 1 ELSE 0 END) AS pending_suggestions
            FROM review_suggestions s
            JOIN review_subtitle_segments seg ON seg.id = s.segment_id
            WHERE s.user_id = ? AND {_withdrawn_filter('s')}
            """,
            (user_id,),
        ).fetchone()
        completed = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM review_video_progress
            WHERE user_id = ? AND completed = 1
            """,
            (user_id,),
        ).fetchone()["count"]
        videos = connection.execute(
            f"""
            SELECT
                v.youtube_video_id,
                v.title,
                COUNT(s.id) AS suggestions_sent,
                COALESCE(SUM(s.changed_chars), 0) AS changed_chars,
                SUM(CASE WHEN s.status = 'approved' THEN 1 ELSE 0 END) AS approved_suggestions,
                SUM(CASE WHEN s.status = 'pending' THEN 1 ELSE 0 END) AS pending_suggestions,
                MAX(COALESCE(p.completed, 0)) AS completed
            FROM review_suggestions s
            JOIN review_subtitle_segments seg ON seg.id = s.segment_id
            JOIN review_videos v ON v.youtube_video_id = seg.youtube_video_id
            LEFT JOIN review_video_progress p
                ON p.user_id = s.user_id
               AND p.youtube_video_id = seg.youtube_video_id
            WHERE s.user_id = ? AND {_withdrawn_filter('s')}
            GROUP BY v.youtube_video_id, v.title
            ORDER BY MAX(s.updated_at) DESC
            """,
            (user_id,),
        ).fetchall()
    return {
        "user": user,
        "suggestions_sent": int(totals["suggestions_sent"] or 0),
        "changed_chars": int(totals["changed_chars"] or 0),
        "videos_contributed": int(totals["videos_contributed"] or 0),
        "approved_suggestions": int(totals["approved_suggestions"] or 0),
        "pending_suggestions": int(totals["pending_suggestions"] or 0),
        "completed_videos": int(completed),
        "videos": [dict(row) for row in videos],
    }


@router.get("")
def leaderboard(request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request)
    user_id = str(session["user_id"])
    rows = _leaderboard_rows(limit=100)
    ranked = [
        {
            **row,
            "rank": index,
            "is_me": str(row["user_id"]) == user_id,
        }
        for index, row in enumerate(rows, 1)
    ]
    me = next((row for row in ranked if row["is_me"]), None)
    return {"leaderboard": ranked, "me": me}


@router.get("/me")
def my_contributions(request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request)
    return _my_detail(str(session["user_id"]))
