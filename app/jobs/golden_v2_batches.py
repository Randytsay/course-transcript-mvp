"""Durable, unapproved link intake queue in the existing isolated V2 ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, MODES, STAGES, V2Conflict


def _wire(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: dict) -> str:
    return hashlib.sha256(_wire(value).encode()).hexdigest()


def initialize_batch_queue(database: Path) -> None:
    GoldenV2Ledger.open_existing(database)
    with closing(sqlite3.connect(f"{Path(database).resolve().as_uri()}?mode=ro", uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if any(not name.startswith(("v2_", "sqlite_")) for name in tables):
            raise ValueError("refusing to modify a mixed or V1 database")
    with closing(sqlite3.connect(database, timeout=15)) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS v2_link_batches (
                id TEXT PRIMARY KEY, manifest TEXT NOT NULL, actor TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS v2_intake_sources (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, mode TEXT NOT NULL,
                estimates TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
                job_id TEXT, error_code TEXT);
            CREATE TABLE IF NOT EXISTS v2_link_batch_items (
                batch_id TEXT NOT NULL REFERENCES v2_link_batches(id),
                source_id TEXT NOT NULL REFERENCES v2_intake_sources(id),
                PRIMARY KEY(batch_id, source_id));
        """)
        db.commit()


def enqueue_link_batch(*, database: Path, preview: dict, mode: str,
                       estimates: dict[str, dict[str, int]], actor: str,
                       ceiling_cents: int) -> dict:
    files = preview["files"]
    if not files or mode not in MODES or not actor.strip():
        raise ValueError("Media files, mode and owner required")
    if set(estimates) != {item["file_id"] for item in files}:
        raise ValueError("Exact per-file estimates required")
    for cost in estimates.values():
        if (set(cost) != set(STAGES) or any(type(x) is not int or x < 0 for x in cost.values())
                or sum(cost.values()) > ceiling_cents):
            raise ValueError("Invalid or over-limit per-file estimates")
    manifest = {"preview_sha256": preview["preview_sha256"], "files": files,
                "mode": mode, "estimates": estimates}
    batch_id = "v2b_" + _sha(manifest)[:32]
    initialize_batch_queue(database)
    with closing(sqlite3.connect(database, timeout=15, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            previous = db.execute("SELECT manifest FROM v2_link_batches WHERE id=?", (batch_id,)).fetchone()
            if previous and previous[0] != _wire(manifest):
                raise V2Conflict("Batch identity conflict")
            if not previous:
                pending = db.execute("SELECT COUNT(*) FROM v2_intake_sources WHERE state IN ('PENDING','PROCESSING')").fetchone()[0]
                if pending + len(files) > 500:
                    raise V2Conflict("Intake queue capacity reached")
                db.execute("INSERT INTO v2_link_batches(id,manifest,actor) VALUES (?,?,?)",
                           (batch_id, _wire(manifest), actor))
                for item in files:
                    source_id = "v2i_" + _sha({"file_id": item["file_id"], "version": item["version"], "mode": mode})[:32]
                    stored = db.execute("SELECT source,estimates FROM v2_intake_sources WHERE id=?", (source_id,)).fetchone()
                    cost = _wire(estimates[item["file_id"]])
                    if stored and (stored[0] != _wire(item) or stored[1] != cost):
                        raise V2Conflict("Existing source version has different intake settings")
                    db.execute("INSERT OR IGNORE INTO v2_intake_sources(id,source,mode,estimates) VALUES (?,?,?,?)",
                               (source_id, _wire(item), mode, cost))
                    db.execute("INSERT INTO v2_link_batch_items VALUES (?,?)", (batch_id, source_id))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return read_link_batch(database, batch_id)


def read_link_batch(database: Path, batch_id: str) -> dict | None:
    with closing(sqlite3.connect(f"{Path(database).resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_link_batches'").fetchone():
            return None
        row = db.execute("SELECT id,created_at FROM v2_link_batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            return None
        items = []
        for item in db.execute("""SELECT s.*,j.status AS job_status,j.revision,j.estimated_cents
                FROM v2_link_batch_items b JOIN v2_intake_sources s ON s.id=b.source_id
                LEFT JOIN v2_jobs j ON j.id=s.job_id WHERE b.batch_id=? ORDER BY s.id""", (batch_id,)):
            source = json.loads(item["source"])
            items.append({"source_id": item["id"], **source, "intake_state": item["state"],
                          "job_id": item["job_id"], "job_status": item["job_status"],
                          "revision": item["revision"], "estimated_cents": item["estimated_cents"],
                          "error_code": item["error_code"]})
    states = {item["intake_state"] for item in items}
    status = ("INTAKE_PENDING" if states & {"PENDING", "PROCESSING"} else
              "INTAKE_REVIEW_REQUIRED" if "FAILED" in states else "INTAKE_COMPLETE")
    return {"batch_id": batch_id, "created_at": row["created_at"], "status": status,
            "items": items, "batch_cost_approval_recorded": False,
            "intake_starts_providers": False, "intake_mutates_drive": False}
