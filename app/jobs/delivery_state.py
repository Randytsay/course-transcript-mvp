"""Transactional database observability for successful Drive delivery."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.jobs.completion import refresh_batch_state
from app.jobs.store import JobStore


def _event_is_duplicate(
    rows: list[Any],
    *,
    source: str,
    published_revision: int | None,
) -> bool:
    if source != "editor":
        return bool(rows)
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("published_revision") == published_revision:
            return True
    return False


def record_delivery_success(
    database_path: Path,
    *,
    job_id: str,
    actor: str,
    source: str,
    backup_count: int,
    published_revision: int | None = None,
) -> dict[str, Any]:
    """Update visible state and append one idempotent delivery event.

    An explicit editor publication is the human decision that releases a
    review-blocked job. It therefore transitions ``awaiting_review`` to
    ``completed`` and refreshes the parent batch in the same transaction.
    """
    store = JobStore(database_path)
    now = datetime.now(UTC).isoformat()
    if source == "editor":
        if published_revision is None:
            raise ValueError("Editor delivery success requires a published revision")
        detail = (
            "辨識、校正與 QA 已完成；人工確認的字幕已安全輸出至原始 Drive 資料夾"
        )
        event_type = "job_drive_editor_published"
    else:
        detail = (
            "辨識、校正與 QA 已完成；Drive 延後回寫已重試成功並完成驗證"
        )
        event_type = "job_drive_delivery_completed"

    with store.transaction() as connection:
        row = connection.execute(
            "SELECT status, stage_detail, batch_id FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Job not found")
        if str(row["status"]) not in {"completed", "awaiting_review"}:
            raise RuntimeError("Drive delivery success can update only a completed job")
        if source != "editor" and str(row["status"]) == "awaiting_review":
            raise RuntimeError(
                "Background delivery cannot complete a human-review-blocked job"
            )

        prior_events = connection.execute(
            """
            SELECT payload_json
            FROM job_events
            WHERE job_id = ? AND event_type = ?
            ORDER BY id
            """,
            (job_id, event_type),
        ).fetchall()
        duplicate = _event_is_duplicate(
            list(prior_events),
            source=source,
            published_revision=published_revision,
        )

        if source == "editor":
            connection.execute(
                """
                UPDATE jobs
                SET status = 'completed', active_stage = 'completed',
                    stage_detail = ?, progress = 100, error = NULL,
                    updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (detail, now, job_id),
            )
        elif str(row["stage_detail"] or "") != detail or not duplicate:
            connection.execute(
                """
                UPDATE jobs
                SET stage_detail = ?, error = NULL, updated_at = ?,
                    revision = revision + 1
                WHERE id = ?
                """,
                (detail, now, job_id),
            )

        if not duplicate:
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
                    "human_review_released": source == "editor",
                },
            )
        if row["batch_id"]:
            refresh_batch_state(connection, str(row["batch_id"]), now)
    return store.get_job(job_id)
