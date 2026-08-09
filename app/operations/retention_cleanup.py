"""Auditable cleanup for unreferenced GCS objects and Drive backups.

Dry-run is the default.  ``--apply`` is required for deletion and every
candidate/result is written to a report, so a VPS cron can be reviewed safely.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _object_name(value: object) -> str:
    text = str(value or "")
    return text.split("gs://", 1)[-1].split("/", 1)[-1] if text.startswith("gs://") else text


def build_report(data_dir: Path, *, now: datetime | None = None, gcs_days: int = 7, drive_days: int = 30, apply: bool = False) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    report: dict[str, Any] = {"generated_at": now.isoformat(), "apply": apply, "gcs": {"candidates": [], "deleted": [], "errors": []}, "drive_backups": {"candidates": [], "deleted": [], "errors": []}}
    referenced: set[str] = set()
    protected_prefixes: set[str] = set()
    active_job_ids: set[str] = set()
    jobs = data_dir / "jobs"
    database = data_dir / "course-transcript.db"
    if database.is_file():
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            active_job_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM jobs WHERE status NOT IN ('completed','cancelled','failed','awaiting_review')"
                ).fetchall()
            }
            connection.close()
        except sqlite3.Error:
            # Fail closed for apply mode: an unreadable job database means we
            # cannot prove provider objects are safe to remove.
            if apply:
                report["gcs"]["errors"].append({"error": "database_unreadable", "message": "active jobs could not be verified"})
    for manifest_path in jobs.glob("*/chunks/chunk-*/manifest.json"):
        payload = _read(manifest_path) or {}
        for key in ("input_object_name", "output_object_name", "gcs_uri"):
            value = payload.get(key)
            if value:
                referenced.add(_object_name(value))
        if payload.get("output_prefix"):
            protected_prefixes.add(_object_name(payload["output_prefix"]).rstrip("/") + "/")
    bucket_name = (os.environ.get("CHIRP_GCS_BUCKET") or os.environ.get("GCS_BUCKET") or "").strip()
    if bucket_name:
        try:
            from google.cloud import storage  # type: ignore
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            cutoff = now - timedelta(days=max(1, gcs_days))
            for blob in client.list_blobs(bucket):
                name = str(blob.name)
                job_id = name.split("/", 2)[1] if name.startswith("jobs/") and "/" in name else ""
                if (
                    not (name.startswith("jobs/") or name.startswith("chirp/"))
                    or name in referenced
                    or any(name.startswith(prefix) for prefix in protected_prefixes)
                    or job_id in active_job_ids
                ):
                    continue
                updated = blob.updated
                if updated and updated < cutoff:
                    entry = {"name": name, "updated": updated.isoformat(), "size": int(blob.size or 0)}
                    report["gcs"]["candidates"].append(entry)
                    if apply:
                        try:
                            blob.delete()
                            report["gcs"]["deleted"].append(name)
                        except Exception as exc:  # pragma: no cover - provider-specific
                            report["gcs"]["errors"].append({"name": name, "error": type(exc).__name__})
        except Exception as exc:
            report["gcs"]["errors"].append({"error": type(exc).__name__, "message": "GCS inventory unavailable"})
    cutoff = now - timedelta(days=max(1, drive_days))
    for state_path in jobs.glob("*/drive-publish-state.json"):
        state = _read(state_path) or {}
        raw_files = state.get("files")
        files = list(raw_files.values()) if isinstance(raw_files, dict) else raw_files if isinstance(raw_files, list) else []
        if state.get("status") != "completed":
            continue
        for item in files:
            if not isinstance(item, dict) or not item.get("backup_remote_path"):
                continue
            created = item.get("backup_created_at")
            try:
                old = datetime.fromisoformat(str(created).replace("Z", "+00:00")) if created else now
            except ValueError:
                old = now
            if old.tzinfo is None:
                old = old.replace(tzinfo=UTC)
            if old >= cutoff:
                continue
            remote = str(item["backup_remote_path"])
            entry = {"job_id": state_path.parent.name, "remote_path": remote, "created_at": old.isoformat()}
            report["drive_backups"]["candidates"].append(entry)
            if apply:
                try:
                    subprocess.run(["rclone", "deletefile", remote], check=True, capture_output=True, text=True, timeout=120)
                    report["drive_backups"]["deleted"].append(remote)
                except Exception as exc:  # pragma: no cover - host-specific
                    report["drive_backups"]["errors"].append({"path": remote, "error": type(exc).__name__})
    report["status"] = "PASS" if not report["gcs"]["errors"] and not report["drive_backups"]["errors"] else "REVIEW"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data")))
    parser.add_argument("--gcs-retention-days", type=int, default=7)
    parser.add_argument("--drive-backup-retention-days", type=int, default=30)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.data_dir, gcs_days=args.gcs_retention_days, drive_days=args.drive_backup_retention_days, apply=args.apply)
    output = args.output or args.data_dir / "retention-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"RETENTION={report['status']} gcs={len(report['gcs']['candidates'])} drive={len(report['drive_backups']['candidates'])} apply={args.apply}")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
