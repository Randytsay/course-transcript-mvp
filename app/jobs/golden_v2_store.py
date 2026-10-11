"""V2 isolated durable stage ledger, intentionally not wired to production.

No provider network, Drive mutation, or existing V1 schema migration. Store
only source identity, mode, stage gates, evidence digests and reversible state,
not private audio/transcript text or credentials. The production implementation
must reuse existing lease/cost/pub governance before activating the V2 worker.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_stage_contract import (
    STAGES, V2Selection, assert_stage_can_advance, next_stage,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _evidence_digest(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", value.lower()):
        raise ValueError("Evidence requires verified content SHA256")
    return value.lower()


class GoldenV2StageStore:
    """A provider-free resumable record; not an authorization service."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS golden_v2_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_sha256 TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    selection_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS golden_v2_stage_evidence (
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    gate_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, stage),
                    UNIQUE(job_id, stage_index),
                    FOREIGN KEY(job_id) REFERENCES golden_v2_jobs(job_id)
                );
            """)

    def create_job(self, job_id: str, selection: V2Selection) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{5,120}", job_id):
            raise ValueError("Invalid V2 job identifier")
        payload = json.dumps({
            "source_sha256": selection.source_sha256.lower(),
            "mode": selection.mode,
            "model_primary": selection.model_primary,
            "model_chirp": selection.model_chirp,
            "model_transcribe": selection.model_transcribe,
        }, sort_keys=True)
        with self._db() as db:
            current = db.execute(
                "SELECT selection_json FROM golden_v2_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if current is not None:
                if current["selection_json"] != payload:
                    raise ValueError("Job identity is already bound to another source/mode")
                return False
            db.execute(
                "INSERT INTO golden_v2_jobs VALUES (?, ?, ?, ?, ?)",
                (job_id, selection.source_sha256.lower(), selection.mode, payload, _now())
            )
        return True

    def status(self, job_id: str) -> dict[str, Any]:
        with self._db() as db:
            job = db.execute(
                "SELECT source_sha256,mode FROM golden_v2_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if job is None:
                raise ValueError("Unknown V2 job")
            stages = db.execute(
                "SELECT stage, stage_index, evidence_sha256, gate_json "
                "FROM golden_v2_stage_evidence WHERE job_id=? ORDER BY stage_index",
                (job_id,),
            ).fetchall()
            last = stages[-1] if stages else None
            return {
                "job_id": job_id,
                "source_sha256": job["source_sha256"],
                "mode": job["mode"],
                "last_stage": last["stage"] if last else None,
                "next_stage": next_stage(last["stage"] if last else None, transcribe_policy="ALL"),
                "completed_stages": [item["stage"] for item in stages],
                "last_gate": json.loads(last["gate_json"]) if last else {},
                "last_evidence_sha256": last["evidence_sha256"] if last else None,
            }

    def record_stage(
        self,
        job_id: str,
        *,
        stage: str,
        evidence_sha256: str,
        gate: dict[str, Any] | None = None,
    ) -> bool:
        """Return False on exact idempotent replay, reject conflicting history.

        Gates and evidence are stored in a transaction. Production will need
        per-stage claims/attempts and reconciliation before calling providers.
        """
        digest = _evidence_digest(evidence_sha256)
        if stage not in STAGES:
            raise ValueError("Unknown stage")
        safe_gate = dict(gate or {})
        if set(safe_gate) - {"primary_candidate_complete", "acoustic_gate_pass", "golden_qa_pass", "transcribe_skipped"}:
            raise ValueError("Gate evidence cannot store unapproved arbitrary payloads")
        if any(type(x) is not bool for x in safe_gate.values()):
            raise ValueError("Gate status values must be booleans")
        payload = json.dumps(safe_gate, sort_keys=True)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            records = db.execute(
                "SELECT stage, stage_index, evidence_sha256, gate_json "
                "FROM golden_v2_stage_evidence WHERE job_id=? ORDER BY stage_index",
                (job_id,),
            ).fetchall()
            if db.execute(
                "SELECT 1 FROM golden_v2_jobs WHERE job_id=?", (job_id,)
            ).fetchone() is None:
                raise ValueError("Unknown V2 job")
            indexed = {row["stage"]: row for row in records}
            if stage in indexed:
                row = indexed[stage]
                if row["evidence_sha256"] == digest and row["gate_json"] == payload:
                    return False
                raise ValueError("Conflicting stage result would replace immutable evidence")
            previous = records[-1] if records else None
            expected = next_stage(
                previous["stage"] if previous else None, transcribe_policy="ALL"
            )
            if stage != expected:
                raise ValueError(f"Stage must advance to {expected}, not {stage}")
            if previous is not None:
                assert_stage_can_advance(
                    completed_stage=previous["stage"],
                    next_requested=stage,
                    evidence=json.loads(previous["gate_json"]),
                )
            db.execute(
                "INSERT INTO golden_v2_stage_evidence VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, stage, STAGES.index(stage), digest, payload, _now()),
            )
        return True
