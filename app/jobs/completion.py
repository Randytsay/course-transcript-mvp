"""Policy-aware completion for paid transcription jobs.

Jobs that require human review must stop at ``awaiting_review`` regardless of
how many review terms were generated. Jobs that explicitly opt out of human
review may complete and publish automatically.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _review_required(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def refresh_batch_state(connection: Any, batch_id: str, now: str) -> None:
    """Refresh a batch without treating reviewed and published as equivalent."""
    rows = connection.execute(
        "SELECT status FROM jobs WHERE batch_id = ? ORDER BY queue_position",
        (batch_id,),
    ).fetchall()
    if not rows:
        return

    statuses = [str(row["status"]) for row in rows]
    ready = sum(status in {"awaiting_review", "completed"} for status in statuses)
    completed = sum(status == "completed" for status in statuses)
    awaiting_review = sum(status == "awaiting_review" for status in statuses)
    failed = sum(status == "failed" for status in statuses)
    cancelled = sum(status == "cancelled" for status in statuses)
    terminal = ready + failed + cancelled

    if terminal == len(statuses):
        if completed == len(statuses):
            batch_status = "completed"
        elif awaiting_review and not failed and not cancelled:
            batch_status = "awaiting_review"
        elif cancelled == len(statuses):
            batch_status = "cancelled"
        elif failed == len(statuses):
            batch_status = "failed"
        elif ready:
            batch_status = "partial_failure" if failed else "partial_cancelled"
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
        (batch_status, ready, failed, now, batch_id),
    )


def finish_with_policy(
    self: Any,
    *,
    job_id: str,
    worker_id: str,
    drive_published: bool = False,
    drive_publication_error: str | None = None,
) -> dict[str, Any]:
    """Finish a job according to its persisted human-review requirement."""
    now = _iso()
    with self.transaction() as connection:
        self._require_lease(connection, job_id, worker_id)
        row = connection.execute(
            "SELECT batch_id, require_human_review FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Job not found")

        requires_review = _review_required(row["require_human_review"])
        if requires_review:
            status = "awaiting_review"
            active_stage = "review"
            detail = "本機輸出與 QA 已完成，等待人工審查與明確發布"
            event_type = "local_outputs_ready_for_review"
            event_payload = {
                "drive_upload_started": bool(drive_published),
                "drive_publication_error": drive_publication_error,
                "human_review_blocking": True,
                "subtitle_review_status": "pending",
                "unexpected_pre_review_publish": bool(drive_published),
            }
        else:
            status = "completed"
            active_stage = "completed"
            if drive_published:
                detail = "辨識、校正與 QA 已完成；選定輸出已安全發布至原始 Drive 資料夾"
                event_type = "job_completed_drive_published"
            elif drive_publication_error:
                detail = "辨識、校正與 QA 已完成；Drive 回寫待重試，不會重跑付費辨識"
                event_type = "job_completed_drive_pending"
            else:
                detail = "辨識、校正與 QA 已完成；未要求 Drive 回寫"
                event_type = "job_completed_local"
            event_payload = {
                "drive_upload_started": bool(drive_published),
                "drive_publication_error": drive_publication_error,
                "human_review_blocking": False,
                "subtitle_review_status": "not_required",
            }

        connection.execute(
            """
            UPDATE jobs
            SET status = ?, active_stage = ?, stage_detail = ?, progress = 100,
                error = NULL, updated_at = ?, revision = revision + 1
            WHERE id = ?
            """,
            (status, active_stage, detail, now, job_id),
        )
        self._clear_lease(connection, job_id, worker_id)
        self._event(
            connection,
            job_id,
            event_type,
            worker_id,
            event_payload,
        )
        if row["batch_id"]:
            refresh_batch_state(connection, str(row["batch_id"]), now)
    return self.get_job(job_id)


def install_completion_patch(job_store_class: type[Any]) -> None:
    """Install policy-aware completion in a worker process."""
    job_store_class.finish_for_review = finish_with_policy  # type: ignore[method-assign]
