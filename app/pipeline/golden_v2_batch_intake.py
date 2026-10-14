"""Restartable, non-paid batch intake. An exclusive file lock serializes workers."""
from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.jobs.drive_api import DriveApiError, download_drive_media
from app.jobs.golden_v2_batches import initialize_batch_queue
from app.pipeline.golden_v2_drive_intake import SourceIntakeBusyError, intake_drive_source


def run_batch_intake_pass(*, database: Path, artifact_root: Path, ceiling_cents: int,
                          intake=intake_drive_source, download=download_drive_media) -> dict:
    root = Path(artifact_root).resolve()
    if Path(artifact_root).is_symlink() or Path(database).is_symlink() or Path(database).resolve().parent != root:
        raise ValueError("Isolated V2 root required")
    initialize_batch_queue(database)
    lock_path = root / "batch-intake.lock"
    if lock_path.is_symlink():
        raise ValueError("Intake lock cannot be a symlink")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"action": "INTAKE_BUSY"}
        with closing(sqlite3.connect(database, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            # Only an exclusive lock holder can recover a killed non-paid intake.
            row = db.execute("SELECT * FROM v2_intake_sources WHERE state IN ('PENDING','PROCESSING') ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return {"action": "INTAKE_IDLE"}
            db.execute("UPDATE v2_intake_sources SET state='PROCESSING',error_code=NULL WHERE id=?", (row["id"],))
            db.commit()
            source = json.loads(row["source"])
            def checked_download(path, destination, **kwargs):
                observation = download(path, destination, **kwargs)
                expected = {"file_id": source["file_id"], "version": source["version"],
                            "size_bytes": source["size_bytes"], "downloaded_md5": source["md5_checksum"],
                            "source_path": source["source_path"]}
                if any(observation.get(k) != v for k, v in expected.items()):
                    raise DriveApiError("Drive source changed since batch preview")
                return observation
            try:
                result = intake(source_path=source["source_path"], artifact_root=root,
                                database=database, mode=row["mode"],
                                estimated_stage_cents=json.loads(row["estimates"]),
                                estimate_ceiling_cents=ceiling_cents, download=checked_download)
                # Do not bind a worker/plugin response to a nonexistent ledger row.
                job = db.execute("SELECT id FROM v2_jobs WHERE id=?", (result["job_id"],)).fetchone()
                if job is None:
                    raise ValueError("Intake returned an unregistered job")
                db.execute("UPDATE v2_intake_sources SET state='COMPLETE',job_id=? WHERE id=?",
                           (result["job_id"], row["id"]))
                action = "SOURCE_PREFLIGHT_COMPLETE"
            except SourceIntakeBusyError:
                db.execute("UPDATE v2_intake_sources SET state='PENDING' WHERE id=?", (row["id"],))
                action = "INTAKE_BUSY"
            except (ValueError, RuntimeError, OSError) as exc:
                # Persist safe codes, never credential-bearing provider messages.
                code = "SOURCE_CHANGED_OR_UNAVAILABLE" if isinstance(exc, DriveApiError) else "PREFLIGHT_FAILED"
                db.execute("UPDATE v2_intake_sources SET state='FAILED',error_code=? WHERE id=?", (code, row["id"]))
                action = code
            db.commit()
            return {"action": action, "source_id": row["id"],
                    "provider_calls_started": False, "drive_mutation_started": False}
