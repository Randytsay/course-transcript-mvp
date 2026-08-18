"""Bounded recovery helpers for interrupted owner AI generation jobs."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from .store import LearningStore

DEFAULT_STALE_MINUTES = 60


def generation_stale_minutes() -> int:
    raw = os.environ.get("LEARNING_GENERATION_STALE_MINUTES", "").strip()
    if not raw:
        return DEFAULT_STALE_MINUTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALE_MINUTES
    return max(15, min(value, 24 * 60))


def recover_stale_generation_jobs(
    store: LearningStore,
    *,
    youtube_video_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Fail abandoned ``running`` rows so a later explicit retry can proceed.

    This does not start a model request and does not alter any learning artifact or
    subtitle. A recent running job remains untouched and will continue to block a
    duplicate generation through ``LearningStore.begin_generation_job``.
    """

    current = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = (current - timedelta(minutes=generation_stale_minutes())).isoformat()
    finished_at = current.isoformat()
    params: list[object] = [
        "AI 產生程序逾時或中斷，系統已將舊的處理中紀錄標記為失敗；重新產生前請先確認沒有另一個執行中的請求。",
        finished_at,
        cutoff,
    ]
    video_clause = ""
    if youtube_video_id:
        video_clause = " AND youtube_video_id = ?"
        params.append(youtube_video_id)
    with store.transaction() as connection:
        cursor = connection.execute(
            f"""
            UPDATE learning_generation_jobs
            SET status = 'failed', error = ?, finished_at = ?
            WHERE status = 'running' AND started_at < ?{video_clause}
            """,
            tuple(params),
        )
        return max(0, int(cursor.rowcount or 0))
