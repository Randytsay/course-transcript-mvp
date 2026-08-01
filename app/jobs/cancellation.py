"""Durable job cancellation with best-effort provider operation cancellation."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from app.jobs.performance import estimated_accrued_cost

RUNNING_STATUSES = {
    "downloading",
    "normalizing",
    "transcribing",
    "merging",
    "segmenting",
    "correcting",
    "exporting",
    "quality_check",
}
DIRECT_CANCEL_STATUSES = {
    "preflight",
    "awaiting_confirmation",
    "queued",
    "paused",
    "failed",
}
TERMINAL_STATUSES = {"awaiting_review", "review", "completed", "cancelled"}
CleanupMode = Literal["preserve", "temporary"]


class CancellationConflict(RuntimeError):
    pass


class CancellationNotFound(LookupError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _event(
    connection: sqlite3.Connection,
    job_id: str,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO job_events(job_id, event_type, actor, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            job_id,
            event_type,
            actor,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            _now(),
        ),
    )


def _accrued(database_path: Path, data_dir: Path, job_id: str) -> Decimal:
    try:
        return estimated_accrued_cost(database_path, data_dir, job_id)
    except Exception:
        # Cancellation must remain available even if optional metrics are damaged.
        return Decimal("0")


def request_cancellation(
    database_path: Path,
    data_dir: Path,
    *,
    job_id: str,
    expected_revision: int,
    reason: str,
    cleanup_mode: CleanupMode,
    actor: str,
) -> dict[str, Any]:
    reason = " ".join(reason.strip().split())[:300] or "使用者要求取消"
    if cleanup_mode not in {"preserve", "temporary"}:
        raise CancellationConflict("Invalid cleanup mode")
    accrued = _accrued(database_path, data_dir, job_id)
    now = _now()
    connection = _connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise CancellationNotFound("Job not found")
        if int(row["revision"]) != int(expected_revision):
            raise CancellationConflict("任務已更新，請重新載入後再操作")
        status = str(row["status"])
        if status == "cancelled":
            connection.commit()
            return dict(row)
        if status == "cancelling":
            connection.commit()
            return dict(row)
        if status in TERMINAL_STATUSES:
            raise CancellationConflict("已完成或待審查的任務不可取消")
        if status not in RUNNING_STATUSES | DIRECT_CANCEL_STATUSES:
            raise CancellationConflict("此任務目前不可取消")

        next_status = "cancelling" if status in RUNNING_STATUSES else "cancelled"
        detail = (
            "正在停止本機程序並嘗試取消已送出的雲端操作"
            if next_status == "cancelling"
            else "任務已取消；未啟動或已停止後續處理"
        )
        connection.execute(
            """
            UPDATE jobs
            SET status = ?, active_stage = 'cancel', stage_detail = ?,
                error = NULL, reserved_cost_usd = ?, updated_at = ?,
                revision = revision + 1
            WHERE id = ?
            """,
            (next_status, detail, str(accrued), now, job_id),
        )
        _event(
            connection,
            job_id,
            "job_cancel_requested",
            actor,
            {
                "previous_status": status,
                "reason": reason,
                "cleanup_mode": cleanup_mode,
                "estimated_accrued_cost_usd": str(accrued),
                "provider_cancellation_pending": next_status == "cancelling",
            },
        )
        if next_status == "cancelled":
            connection.execute(
                """
                UPDATE jobs
                SET locked_by = NULL, lease_expires_at = NULL,
                    last_heartbeat_at = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
            _event(
                connection,
                job_id,
                "job_cancelled",
                actor,
                {
                    "reason": reason,
                    "cleanup_mode": cleanup_mode,
                    "provider_results": [],
                },
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    if next_status == "cancelled" and cleanup_mode == "temporary":
        cleanup_temporary_files(data_dir / "jobs" / job_id)
    return get_job(database_path, job_id)


def get_job(database_path: Path, job_id: str) -> dict[str, Any]:
    with _connect(database_path) as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise CancellationNotFound("Job not found")
    return dict(row)


def cancel_chirp_operations(job_dir: Path) -> list[dict[str, Any]]:
    """Request cancellation for submitted Speech operations.

    Speech cancellation is best effort: Google can complete an operation before
    the cancellation request is processed, and some operation types may return
    UNIMPLEMENTED. The caller must therefore preserve already produced evidence
    and costs.
    """
    operation_names: list[tuple[Path, str]] = []
    for manifest_path in sorted((job_dir / "chunks").glob("chunk-*/manifest.json")):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        status = str(payload.get("status") or "")
        name = str(payload.get("operation_name") or "")
        if name and status not in {"SUCCEEDED", "EMPTY_SILENCE", "FAILED", "CANCELLED"}:
            operation_names.append((manifest_path, name))
    if not operation_names:
        return []

    from google.api_core import exceptions as google_exceptions
    from google.cloud import speech_v2

    client = speech_v2.SpeechClient(
        client_options={"api_endpoint": "us-speech.googleapis.com"}
    )
    results: list[dict[str, Any]] = []
    for manifest_path, operation_name in operation_names:
        outcome = "requested"
        error: str | None = None
        try:
            client.cancel_operation(
                request={"name": operation_name},
                timeout=15,
            )
        except google_exceptions.MethodNotImplemented:
            outcome = "unsupported"
            error = "Provider does not support cancellation for this operation"
        except google_exceptions.GoogleAPICallError as exc:
            outcome = "error"
            error = str(exc)[-300:]
        except Exception as exc:  # Defensive: cancellation must not hide job state.
            outcome = "error"
            error = str(exc)[-300:]

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["cancel_requested_at"] = _now()
            payload["cancel_request_outcome"] = outcome
            if error:
                payload["cancel_request_error"] = error
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(manifest_path)
        except (OSError, json.JSONDecodeError):
            pass
        results.append(
            {
                "chunk": manifest_path.parent.name,
                "outcome": outcome,
                "error": error,
            }
        )
    return results


def finalize_cancellation(
    database_path: Path,
    data_dir: Path,
    *,
    job_id: str,
    worker_id: str,
    provider_results: list[dict[str, Any]],
) -> dict[str, Any]:
    accrued = _accrued(database_path, data_dir, job_id)
    cleanup_mode: CleanupMode = "preserve"
    reason = "使用者要求取消"
    now = _now()
    connection = _connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise CancellationNotFound("Job not found")
        if row["status"] == "cancelled":
            connection.commit()
            return dict(row)
        if row["status"] != "cancelling":
            raise CancellationConflict("Job is not waiting for cancellation")
        event = connection.execute(
            """
            SELECT payload_json FROM job_events
            WHERE job_id = ? AND event_type = 'job_cancel_requested'
            ORDER BY id DESC LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if event:
            try:
                payload = json.loads(event["payload_json"] or "{}")
                cleanup_mode = payload.get("cleanup_mode", "preserve")
                reason = payload.get("reason", reason)
            except json.JSONDecodeError:
                pass
        connection.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', active_stage = 'cancel',
                stage_detail = '任務已取消；保留已完成證據與已發生成本',
                reserved_cost_usd = ?, locked_by = NULL,
                lease_expires_at = NULL, last_heartbeat_at = NULL,
                updated_at = ?, revision = revision + 1
            WHERE id = ?
            """,
            (str(accrued), now, job_id),
        )
        _event(
            connection,
            job_id,
            "job_cancelled",
            worker_id,
            {
                "reason": reason,
                "cleanup_mode": cleanup_mode,
                "provider_results": provider_results,
                "estimated_accrued_cost_usd": str(accrued),
                "warning": "已送出的雲端操作可能在取消生效前完成並產生費用",
            },
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    if cleanup_mode == "temporary":
        cleanup_temporary_files(data_dir / "jobs" / job_id)
    return get_job(database_path, job_id)


def cleanup_temporary_files(job_dir: Path) -> list[str]:
    removed: list[str] = []
    if not job_dir.is_dir():
        return removed
    candidates: list[Path] = []
    candidates.extend(job_dir.glob("source-original.*"))
    candidates.append(job_dir / "normalized.flac")
    candidates.extend(job_dir.rglob("*.tmp"))
    candidates.extend(job_dir.rglob("*.partial"))
    candidates.extend(job_dir.glob("chunks/chunk-*/audio.flac"))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            relative = str(path.relative_to(job_dir))
            path.unlink()
            removed.append(relative)
        except OSError:
            continue
    return sorted(set(removed))
