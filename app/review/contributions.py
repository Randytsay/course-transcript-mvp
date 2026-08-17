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


@router.get("")
def leaderboard(request: Request) -> dict[str, Any]:
    session = require_reviewer_session(request)
    user_id = str(session["user_id"])
    rows = _store().contribution_leaderboard(limit=100)
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
    return _store().user_contribution_detail(str(session["user_id"]))
