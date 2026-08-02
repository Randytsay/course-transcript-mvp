"""Complete paid jobs without forcing a blocking human-review gate.

The transcription pipeline may still preserve review terms and subtitle QA
signals, but those are optional post-processing inputs. A successful paid job
is therefore terminal once local QA and the configured Drive publication step
have finished (or publication has been recorded as retryable).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _refresh_batch_state(connection: Any, batch_id: str, now: str) -> None:
    rows = connection.execute(
        "SELECT status FROM jobs WHERE batch_id = ? ORDER BY queue_position",
        (batch_id,),
    ).fetchall()
    if not rows:
        return
    statuses = [str(row["status"]) for row in rows]
    completed = sum(status in {"completed", "awaiting_review"} for status in statuses)
    failed = sum(status == "failed" for status in statuses)
    cancelled = sum(status == "cancelled" for status in statuses)
    terminal = completed + failed + cancelled

    if terminal == len(statuses):
        if completed == len(statuses):
            batch_status = "completed"
        elif cancelled == len(statuses):
            batch_status = "cancelled"
        elif completed:
            batch_status = "partial_cancelled" if cancelled and not failed else "partial_failure"
        else:
            batch_status = "failed" if failed else "cancelled"
    elif any(status == "cancelling" for status in statuses):
        batch_status = "cancelling"
    elif any(
        status
        in {
            "queued",
            "downloading",
            "normalizing",
            "transcribing",
            "merging",
            "segmenting",
            "correcting",
            "exporting",
            "quality_check",
        }
        for status in statuses
    ):
        batch_status = "processing"
    elif any(status == "paused" for status in statuses):
        batch_status = "paused"
    elif all(status == "awaiting_confirmation" for status in statuses):
        batch_status = "awaiting_confirmation"
    else:
        batch_status = "preflight"

    connection.execute(
        """
        UPDATE batches
        SET status = ?, completed_count = ?, failed_count = ?,
            updated_at = ?, revision = revision + 1
        WHERE id = ?
        """,
        (batch_status, completed, failed, now, batch_id),
    )


def finish_completed(
    self: Any,
    *,
    job_id: str,
    worker_id: str,
    drive_published: bool = False,
    drive_publication_error: str | None = None,
) -> dict[str, Any]:
    """Drop-in replacement for ``JobStore.finish_for_review``.

    The signature intentionally matches the legacy method so the reviewed base
    worker can be reused. Review terms remain available to the subtitle editor,
    but they do not block output or batch completion.
    """

    now = _iso()
    with self.transaction() as connection:
        self._require_lease(connection, job_id, worker_id)
        row = connection.execute(
            "SELECT batch_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Job not found")

        if drive_published:
            detail = "辨識、校正與 QA 已完成；選定字幕已安全輸出至原始 Drive 資料夾"
            event_type = "job_completed_drive_published"
        elif drive_publication_error:
            detail = "辨識、校正與 QA 已完成；Drive 回寫待重試，不會重跑付費辨識"
            event_type = "job_completed_drive_pending"
        else:
            detail = "辨識、校正與 QA 已完成；未要求 Drive 回寫"
            event_type = "job_completed_local"

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
        self._clear_lease(connection, job_id, worker_id)
        self._event(
            connection,
            job_id,
            event_type,
            worker_id,
            {
                "drive_upload_started": drive_published,
                "drive_publication_error": drive_publication_error,
                "human_review_blocking": False,
                "subtitle_review_status": "not_reviewed",
            },
        )
        if row["batch_id"]:
            _refresh_batch_state(connection, str(row["batch_id"]), now)
    return self.get_job(job_id)


def install_completion_patch(job_store_class: type[Any]) -> None:
    """Install the non-blocking completion policy in a worker process only."""

    job_store_class.finish_for_review = finish_completed  # type: ignore[method-assign]
