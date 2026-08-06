#!/usr/bin/env python3
"""Explain delayed Drive delivery candidate selection without mutating state."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EDITOR_OWNED = {
    "editor_publish_in_progress",
    "editor_publish_failed",
    "superseded_by_editor",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_time(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def diagnose(data_dir: Path, requested_job_id: str | None) -> list[dict[str, Any]]:
    database = data_dir / "course-transcript.db"
    if not database.is_file():
        raise SystemExit(f"DIAGNOSE_FAIL database missing: {database}")

    uri = f"file:{database}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, status, source_path, updated_at, created_at
            FROM jobs
            WHERE status IN ('completed', 'awaiting_review')
              AND source_path LIKE 'gdrive:%'
            ORDER BY updated_at, created_at
            """
        ).fetchall()
    finally:
        connection.close()

    now = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    selected = False
    for row in rows:
        job_id = str(row["id"])
        if requested_job_id and job_id != requested_job_id:
            continue
        job_dir = data_dir / "jobs" / job_id
        delivery = read_json(job_dir / "drive-delivery-state.json")
        manifest = read_json(job_dir / "pipeline-manifest.json")
        publish = read_json(job_dir / "drive-publish-state.json")

        delivery_status = str(delivery.get("status") or "")
        next_attempt = parse_time(delivery.get("next_attempt_at"))
        due = next_attempt is None or next_attempt <= now
        editor_owned = delivery_status in EDITOR_OWNED
        manifest_pending = manifest.get("drive_publication_status") == "pending_retry"
        publish_incomplete = bool(publish) and publish.get("status") != "completed"
        eligible = (
            not editor_owned
            and (manifest_pending or publish_incomplete)
            and due
        )
        would_be_selected = eligible and not selected
        if would_be_selected:
            selected = True

        false_conditions: list[str] = []
        if editor_owned:
            false_conditions.append("editor_owned")
        if not manifest_pending:
            false_conditions.append("manifest_not_pending_retry")
        if not publish_incomplete:
            false_conditions.append("publish_state_not_incomplete")
        if not due:
            false_conditions.append("retry_not_due")
        if eligible and not would_be_selected:
            false_conditions.append("earlier_eligible_candidate_selected_first")

        results.append(
            {
                "job_id": job_id,
                "db_status": str(row["status"]),
                "job_dir_exists": job_dir.is_dir(),
                "delivery_status": delivery_status or None,
                "attempts": int(delivery.get("attempts", 0) or 0),
                "next_attempt_at": delivery.get("next_attempt_at"),
                "retry_due": due,
                "manifest_delivery_status": manifest.get("drive_publication_status"),
                "publish_state_present": bool(publish),
                "publish_state_status": publish.get("status"),
                "editor_owned": editor_owned,
                "eligible": eligible,
                "would_be_selected": would_be_selected,
                "false_conditions": false_conditions,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"),
    )
    parser.add_argument("--job-id")
    args = parser.parse_args()

    results = diagnose(Path(args.data_dir), args.job_id)
    print(json.dumps({"candidate_count": len(results)}, ensure_ascii=False))
    for result in results:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.job_id and not results:
        print(json.dumps({"requested_job_found": False}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
