"""Durable SQLite state helpers for non-blocking Chirp dynamic batching."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def count_waiting_dynamic(store: Any) -> int:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM jobs
            WHERE status = 'transcribing'
              AND active_stage = 'chirp'
              AND approved_at IS NOT NULL
            """
        ).fetchone()
    return int(row["count"] if row else 0)


def next_waiting_dynamic(store: Any) -> dict[str, Any] | None:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'transcribing'
              AND active_stage = 'chirp'
              AND approved_at IS NOT NULL
              AND locked_by IS NULL
            ORDER BY updated_at, created_at, batch_id, queue_position
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row is not None else None


def next_fresh_queued(store: Any) -> dict[str, Any] | None:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued'
              AND approved_at IS NOT NULL
              AND CAST(reserved_cost_usd AS REAL) > 0
              AND locked_by IS NULL
            ORDER BY created_at, batch_id, queue_position
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row is not None else None


def mark_dynamic_waiting(
    store: Any,
    *,
    job_id: str,
    worker_id: str,
    chunk_count: int,
) -> dict[str, Any]:
    now = _iso()
    with store.transaction() as connection:
        store._require_lease(connection, job_id, worker_id)
        row = connection.execute(
            "SELECT batch_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Job not found")
        connection.execute(
            """
            UPDATE jobs
            SET status = 'transcribing', active_stage = 'chirp',
                stage_detail = ?, progress = 45, updated_at = ?,
                revision = revision + 1
            WHERE id = ?
            """,
            (
                f"Chirp 動態批次已提交 {chunk_count} 段；等待 Google 離峰處理",
                now,
                job_id,
            ),
        )
        store._clear_lease(connection, job_id, worker_id)
        store._event(
            connection,
            job_id,
            "chirp_dynamic_batch_submitted",
            worker_id,
            {
                "chunk_count": chunk_count,
                "processing_strategy": "DYNAMIC_BATCHING",
                "worker_released": True,
            },
        )
        if row["batch_id"]:
            store._refresh_batch_state(connection, row["batch_id"], now)
    return store.get_job(job_id)


def touch_dynamic_waiting(
    store: Any,
    *,
    job_id: str,
    worker_id: str,
    completed_chunks: int,
    total_chunks: int,
) -> dict[str, Any]:
    now = _iso()
    with store.transaction() as connection:
        store._require_lease(connection, job_id, worker_id)
        connection.execute(
            """
            UPDATE jobs
            SET stage_detail = ?, progress = ?, updated_at = ?,
                revision = revision + 1
            WHERE id = ?
            """,
            (
                f"Chirp 動態批次等待中：{completed_chunks}/{total_chunks} 分段完成",
                min(61, 45 + round(16 * completed_chunks / max(1, total_chunks))),
                now,
                job_id,
            ),
        )
        store._clear_lease(connection, job_id, worker_id)
        store._event(
            connection,
            job_id,
            "chirp_dynamic_batch_checked",
            worker_id,
            {
                "completed_chunks": completed_chunks,
                "total_chunks": total_chunks,
            },
        )
    return store.get_job(job_id)
