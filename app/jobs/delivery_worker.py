"""Retry only Drive delivery for completed jobs; never repeat paid providers."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.drive_publish import DrivePublishError, publish_outputs, source_parent_destination
from app.providers.hardening_common import atomic_json, iso, parse_time, retry_delay_seconds, utcnow

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
DATABASE = DATA_DIR / "course-transcript.db"


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _due(job_dir: Path) -> bool:
    state = _read(job_dir / "drive-delivery-state.json")
    next_at = parse_time(state.get("next_attempt_at"))
    return next_at is None or next_at <= utcnow()


def _candidate() -> dict[str, Any] | None:
    if not DATABASE.is_file():
        return None
    connection = sqlite3.connect(DATABASE, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'completed'
              AND source_path LIKE 'gdrive:%'
            ORDER BY updated_at, created_at
            LIMIT 500
            """
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        record = dict(row)
        job_dir = DATA_DIR / "jobs" / record["id"]
        manifest = _read(job_dir / "pipeline-manifest.json")
        publish_state = _read(job_dir / "drive-publish-state.json")
        pending = manifest.get("drive_publication_status") == "pending_retry"
        incomplete = bool(publish_state) and publish_state.get("status") != "completed"
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


def _mark_completed(job_dir: Path, result: dict[str, Any]) -> None:
    atomic_json(
        job_dir / "drive-delivery-state.json",
        {
            "status": "completed",
            "attempts": int(_read(job_dir / "drive-delivery-state.json").get("attempts", 0)) + 1,
            "completed_at": iso(),
            "next_attempt_at": None,
            "backup_count": result.get("backup_count", 0),
        },
    )
    _write_manifest_status(job_dir, "completed", None)


def run_once() -> bool:
    record = _candidate()
    if record is None:
        return False
    job_dir = DATA_DIR / "jobs" / record["id"]
    try:
        output_formats = json.loads(record.get("output_formats_json") or '["srt","txt"]')
        with drive_publish_lock(DATA_DIR, str(record["source_path"])):
            result = publish_outputs(
                job_dir,
                source_name=str(record["source_name"]),
                destination=source_parent_destination(str(record["source_path"])),
                output_formats=output_formats,
                authorized=True,
            )
        _mark_completed(job_dir, result)
        print(f"DRIVE_DELIVERY=PASS job={record['id']}")
    except (DrivePublishError, OSError, ValueError, json.JSONDecodeError) as exc:
        state = _schedule_failure(job_dir, f"{type(exc).__name__}: {exc}")
        _write_manifest_status(job_dir, "pending_retry", state["last_error"])
        print(
            f"DRIVE_DELIVERY=RETRY job={record['id']} attempts={state['attempts']} "
            f"next={state['next_attempt_at']}"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=60)
    args = parser.parse_args()
    if args.once:
        run_once()
        return 0
    while True:
        worked = run_once()
        time.sleep(1 if worked else max(5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
