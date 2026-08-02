"""Transactional database observability for successful Drive delivery."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.jobs.store import JobStore


def record_delivery_success(
    database_path: Path,
    *,
    job_id: str,
    actor: str,
    source: str,
    backup_count: int,
    published_revision: int | None = None,
) -> dict[str, Any]:
    """Update the visible job detail and append one deduplicated success event."""
    store = JobStore(database_path)
    now = datetime.now(UTC).isoformat()
    if source == "editor":
        detail = (
            "辨識、校正與 QA 已完成；人工校訂字幕已安全輸出至原始 Drive 資料夾"
        )
        event_type = "job_drive_editor_published"
    else:
        detail = (
            "辨識、校正與 QA 已完成；Drive 延後回寫已重試成功並完成驗證"
        )
        event_type = "job_drive_delivery_completed"

    with store.transaction() as connection:
        row = connection.execute(
            "SELECT status, stage_detail FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Job not found")
        if str(row["status"]) not in {"completed", "awaiting_review"}:
            raise RuntimeError("Drive delivery success can update only a completed job")

        duplicate = connection.execute(
            """
            SELECT 1
            FROM job_events
            WHERE job_id = ? AND event_type = ?
            LIMIT 1
            """,
            (job_id, event_type),
        ).fetchone()
        connection.execute(
            """
            UPDATE jobs
            SET stage_detail = ?, error = NULL, updated_at = ?,
                revision = revision + 1
            WHERE id = ?
            """,
            (detail, now, job_id),
        )
        if duplicate is None:
            store._event(
                connection,
                job_id,
                event_type,
                actor,
                {
                    "source": source,
                    "backup_count": int(backup_count),
                    "published_revision": published_revision,
                    "paid_provider_repeated": False,
                },
            )
    return store.get_job(job_id)
