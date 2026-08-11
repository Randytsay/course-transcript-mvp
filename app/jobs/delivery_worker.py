"""Retry Drive delivery without bypassing a required human-review gate."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.jobs.delivery_state import record_delivery_success
from app.jobs.drive_lock import drive_publish_lock
from app.jobs.drive_publish import (
    DrivePublishError,
    publish_outputs,
    source_parent_destination,
)
from app.providers.hardening_common import (
    atomic_json,
    iso,
    parse_time,
    retry_delay_seconds,
    utcnow,
)
from app.operations.runtime_heartbeat import write_service_heartbeat

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
DATABASE = DATA_DIR / "course-transcript.db"
_EDITOR_OWNED_DELIVERY_STATES = {
    "editor_publish_in_progress",
    "editor_publish_failed",
    "superseded_by_editor",
}
_DELIVERY_ERRORS = (
    DrivePublishError,
    ValueError,
    LookupError,
    RuntimeError,
    sqlite3.Error,
    json.JSONDecodeError,
)


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _review_required(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _write_manifest_status(job_dir: Path, status: str, error: str | None) -> None:
    for name in ("pipeline-manifest.json", "processing_manifest.json"):
        path = job_dir / name
        payload = _read(path)
        if not payload:
            continue
        payload["drive_publication_status"] = status
        payload["drive_publication_error"] = error
        payload["drive_delivery_updated_at"] = iso()
        atomic_json(path, payload)


def _delivery_state(job_dir: Path) -> dict[str, Any]:
    return _read(job_dir / "drive-delivery-state.json")


def _superseded(job_dir: Path) -> bool:
    return str(_delivery_state(job_dir).get("status") or "") in (
        _EDITOR_OWNED_DELIVERY_STATES
    )


def _due(job_dir: Path) -> bool:
    state = _delivery_state(job_dir)
    next_at = parse_time(state.get("next_attempt_at"))
    return next_at is None or next_at <= utcnow()


def _candidate() -> dict[str, Any] | None:
    if not DATABASE.is_file():
        return None
    connection = sqlite3.connect(DATABASE, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "require_human_review" in columns:
            query = """
                SELECT * FROM jobs
                WHERE source_path LIKE 'gdrive:%'
                  AND (
                        status = 'completed'
                        OR (
                            status = 'awaiting_review'
                            AND require_human_review = 0
                        )
                  )
                ORDER BY updated_at, created_at
            """
        else:
            # Retained databases from before the review flag existed used
            # awaiting_review as a non-blocking terminal state.
            query = """
                SELECT * FROM jobs
                WHERE source_path LIKE 'gdrive:%'
                  AND status IN ('completed', 'awaiting_review')
                ORDER BY updated_at, created_at
            """
        rows = connection.execute(query).fetchall()
    finally:
        connection.close()
    for row in rows:
        record = dict(row)
        if (
            str(record.get("status") or "") == "awaiting_review"
            and "require_human_review" in record
            and _review_required(record.get("require_human_review"))
        ):
            continue
        job_dir = DATA_DIR / "jobs" / record["id"]
        if _superseded(job_dir):
            continue
        manifest = _read(job_dir / "pipeline-manifest.json")
        publish_state = _read(job_dir / "drive-publish-state.json")
        pending = manifest.get("drive_publication_status") == "pending_retry"
        incomplete = (
            bool(publish_state)
            and publish_state.get("status") != "completed"
        )
        if (pending or incomplete) and _due(job_dir):
            return record
    return None


def _schedule_failure(job_dir: Path, error: str) -> dict[str, Any]:
    path = job_dir / "drive-delivery-state.json"
    state = _read(path)
    attempts = int(state.get("attempts", 0)) + 1
    delay = retry_delay_seconds(attempts, base_seconds=60)
    state.update(
        status="pending_retry",
        attempts=attempts,
        last_error=error[-1000:],
        last_attempt_at=iso(),
        next_attempt_at=iso(utcnow() + timedelta(seconds=delay)),
    )
    atomic_json(path, state)
    return state


def _mark_completed(
    record: dict[str, Any],
    job_dir: Path,
    result: dict[str, Any],
) -> None:
    # Update the visible job detail and event log before changing the retry
    # manifest. If the database transaction fails, the completed rclone state
    # remains idempotent and this delivery is selected again for a metadata-only
    # retry without another remote upload.
    record_delivery_success(
        DATABASE,
        job_id=str(record["id"]),
        actor=os.environ.get(
            "COURSE_TRANSCRIPT_DELIVERY_WORKER_ID",
            "drive-delivery-worker-1",
        ),
        source="delivery_worker",
        backup_count=int(result.get("backup_count", 0)),
    )
    atomic_json(
        job_dir / "drive-delivery-state.json",
        {
            "status": "completed",
            "attempts": int(
                _read(job_dir / "drive-delivery-state.json").get("attempts", 0)
            )
            + 1,
            "completed_at": iso(),
            "next_attempt_at": None,
            "backup_count": result.get("backup_count", 0),
        },
    )
    _write_manifest_status(job_dir, "completed", None)


def _record_failure_while_locked(
    record: dict[str, Any],
    job_dir: Path,
    exc: Exception,
) -> None:
    # The caller holds the global Drive lock. The editor cannot change delivery
    # ownership between this recheck and the state write.
    if _superseded(job_dir):
        print(f"DRIVE_DELIVERY=SKIP_EDITOR_OWNED job={record['id']}")
        return
    state = _schedule_failure(job_dir, f"{type(exc).__name__}: {exc}")
    _write_manifest_status(job_dir, "pending_retry", state["last_error"])
    print(
        f"DRIVE_DELIVERY=RETRY job={record['id']} "
        f"attempts={state['attempts']} next={state['next_attempt_at']}"
    )


def run_once() -> bool:
    record = _candidate()
    if record is None:
        return False
    job_dir = DATA_DIR / "jobs" / record["id"]
    source_path = str(record["source_path"])

    # Fail closed even if a future query change accidentally returns a blocked
    # review job. No remote mutation may occur before an explicit editor publish.
    if (
        str(record.get("status") or "") == "awaiting_review"
        and "require_human_review" in record
        and _review_required(record.get("require_human_review"))
    ):
        print(f"DRIVE_DELIVERY=SKIP_HUMAN_REVIEW job={record['id']}")
        return True

    try:
        with drive_publish_lock(DATA_DIR, source_path):
            # Recheck after taking the global lock. Editor intent is persisted
            # before remote mutation, so stale pipeline output cannot be applied.
            if _superseded(job_dir):
                print(f"DRIVE_DELIVERY=SKIP_EDITOR_OWNED job={record['id']}")
                return True
            try:
                output_formats = json.loads(
                    record.get("output_formats_json") or '["srt","txt"]'
                )
                result = publish_outputs(
                    job_dir,
                    source_name=str(record["source_name"]),
                    destination=source_parent_destination(source_path),
                    output_formats=output_formats,
                    authorized=True,
                )
                _mark_completed(record, job_dir, result)
            except _DELIVERY_ERRORS as exc:
                _record_failure_while_locked(record, job_dir, exc)
                return True
        print(f"DRIVE_DELIVERY=PASS job={record['id']}")
    except OSError as exc:
        # Lock acquisition/use failed. Do not mutate delivery ownership without
        # the lock; leave the existing retry state untouched for a later pass.
        print(
            f"DRIVE_DELIVERY=LOCK_RETRY job={record['id']} "
            f"error={type(exc).__name__}"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=60)
    args = parser.parse_args()
    if args.once:
        write_service_heartbeat(DATA_DIR, "delivery-worker", state="once")
        run_once()
        return 0
    while True:
        write_service_heartbeat(DATA_DIR, "delivery-worker")
        worked = run_once()
        time.sleep(1 if worked else max(5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
