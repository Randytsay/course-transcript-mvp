"""Golden V2 isolated SQLite task ledger (not wired into production workers).

Stages have durable, single-claim semantics. A crashed/external paid request
stays RUNNING/OUTCOME_UNKNOWN until evidence is reconciled; it is never
automatically retried. This schema is additive and uses its own v2_* tables.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STAGES = (
    "gemini_primary", "chirp_independent", "compare",
    "third_optional", "adjudication", "golden_qa",
)
MODEL_BY_STAGE = {
    "gemini_primary": "gemini-3.8-flash",
    "chirp_independent": "chirp_3",
    "third_optional": "gemini-3.5-transcribe",
}
MODES = frozenset(("QUALITY_3WAY", "SMART_2PLUS", "FLASH_CHIRP"))


class V2Conflict(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class GoldenV2Ledger:
    def __init__(self, database: Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with sqlite3.connect(self.database, timeout=15, isolation_level=None) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def initialize(self) -> None:
        with self._tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS v2_jobs (
                    id TEXT PRIMARY KEY, source_sha TEXT NOT NULL,
                    mode TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    approval_reference TEXT, approved_cents INTEGER,
                    estimated_cents INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'AWAITING_APPROVAL',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v2_stages (
                    job_id TEXT NOT NULL, ord INTEGER NOT NULL,
                    name TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
                    estimate_cents INTEGER NOT NULL DEFAULT 0,
                    actual_cents INTEGER, claim_hash TEXT, evidence_sha TEXT,
                    review_required INTEGER,
                    updated_at TEXT,
                    PRIMARY KEY(job_id,name),
                    FOREIGN KEY(job_id) REFERENCES v2_jobs(id)
                );
                CREATE TABLE IF NOT EXISTS v2_windows (
                    job_id TEXT NOT NULL, stage_name TEXT NOT NULL,
                    identity_digest TEXT NOT NULL, source_sha TEXT NOT NULL,
                    PRIMARY KEY(job_id,stage_name,identity_digest)
                );
                CREATE TABLE IF NOT EXISTS v2_reconciliations (
                    job_id TEXT NOT NULL, stage_name TEXT NOT NULL,
                    actor TEXT NOT NULL, evidence_sha TEXT NOT NULL,
                    actual_cents INTEGER NOT NULL, verified_at TEXT NOT NULL,
                    PRIMARY KEY(job_id,stage_name,evidence_sha)
                );
            """)

    def create(self, *, source_sha: str, mode: str,
               estimated_stage_cents: dict[str, int]) -> str:
        if len(source_sha) != 64 or any(x not in "0123456789abcdef" for x in source_sha):
            raise ValueError("requires source SHA256")
        if mode not in MODES or set(estimated_stage_cents) != set(STAGES):
            raise ValueError("invalid V2 mode or stage estimates")
        if any(type(v) is not int or v < 0 for v in estimated_stage_cents.values()):
            raise ValueError("invalid integer USD-cent estimate")
        job_id = "v2_" + uuid.uuid4().hex
        with self._tx() as db:
            db.execute(
                "INSERT INTO v2_jobs(id,source_sha,mode,estimated_cents,created_at) VALUES(?,?,?,?,?)",
                (job_id, source_sha, mode, sum(estimated_stage_cents.values()), _now()),
            )
            db.executemany(
                "INSERT INTO v2_stages(job_id,ord,name,estimate_cents) VALUES(?,?,?,?)",
                [(job_id, i, name, estimated_stage_cents[name]) for i, name in enumerate(STAGES)],
            )
        return job_id

    def approve(self, job_id: str, *, revision: int, approved_cents: int,
                reference: str, actor: str) -> None:
        """Owner-authenticated endpoint should call this only after review.

        A caller cannot assert approval through provider adapters. Account
        authorization and real financial controls are external dependencies.
        """
        if type(approved_cents) is not int or approved_cents < 0 or not reference.strip() or not actor.strip():
            raise ValueError("invalid owner approval")
        with self._tx() as db:
            job = self._job(db, job_id)
            if (job["revision"] != revision or job["status"] != "AWAITING_APPROVAL"
                    or approved_cents < job["estimated_cents"]):
                raise V2Conflict("approval revision, state, or amount mismatch")
            db.execute(
                "UPDATE v2_jobs SET status='QUEUED',approval_reference=?,"
                "approved_cents=?,revision=revision+1 WHERE id=?",
                (reference, approved_cents, job_id),
            )

    def _job(self, db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM v2_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise V2Conflict("unknown job")
        return row

    def _stages(self, db: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
        return db.execute("SELECT * FROM v2_stages WHERE job_id=? ORDER BY ord", (job_id,)).fetchall()

    def _skip_third(self, db: sqlite3.Connection, job: sqlite3.Row) -> bool:
        if job["mode"] == "FLASH_CHIRP":
            return True
        if job["mode"] != "SMART_2PLUS":
            return False
        item = db.execute(
            "SELECT state,review_required FROM v2_stages WHERE job_id=? AND name='compare'",
            (job["id"],),
        ).fetchone()
        if item is None or item["state"] != "COMPLETE" or item["review_required"] is None:
            raise V2Conflict("missing persisted comparison decision")
        return not bool(item["review_required"])

    def claim_next(self, job_id: str, *, worker_id: str) -> dict[str, Any]:
        """Claim only the next stage, never re-claim RUNNING or unknown work."""
        if not worker_id.strip():
            raise ValueError("worker identity required")
        with self._tx() as db:
            job = self._job(db, job_id)
            if job["status"] not in {"QUEUED", "PROCESSING"}:
                raise V2Conflict("job not authorized/processing")
            stages = self._stages(db, job_id)
            for row in stages:
                if row["state"] in {"COMPLETE", "SKIPPED"}:
                    continue
                if row["state"] != "PENDING":
                    raise V2Conflict(f"stage {row['name']} unresolved: {row['state']}")
                if row["name"] == "third_optional" and self._skip_third(db, job):
                    db.execute(
                        "UPDATE v2_stages SET state='SKIPPED',actual_cents=0,updated_at=? "
                        "WHERE job_id=? AND name=?", (_now(), job_id, row["name"]),
                    )
                    continue
                committed = sum((x["actual_cents"] or 0) for x in stages)
                if committed + row["estimate_cents"] > job["approved_cents"]:
                    raise V2Conflict("reserved/actual cost exceeds approved amount")
                claim = secrets.token_urlsafe(32)
                db.execute(
                    "UPDATE v2_stages SET state='RUNNING',claim_hash=?,updated_at=? "
                    "WHERE job_id=? AND name=?", (hashlib.sha256(claim.encode()).hexdigest(), _now(), job_id, row["name"]),
                )
                db.execute("UPDATE v2_jobs SET status='PROCESSING',revision=revision+1 WHERE id=?", (job_id,))
                return {"job_id":job_id, "stage":row["name"], "claim":claim,
                        "source_sha":job["source_sha"], "estimate_cents":row["estimate_cents"]}
            db.execute("UPDATE v2_jobs SET status='AWAITING_FINAL_REVIEW' WHERE id=?", (job_id,))
            raise V2Conflict("all stages finished; final owner/QA decision required")

    def register_window(self, claim: dict[str, Any], window: dict[str, Any]) -> None:
        """Stage a source-bound immutable URI/window identity before provider call.

        Caller must independently prove staged audio matches preflight source.
        """
        if not isinstance(window.get("gcs_uri"), str) or not window["gcs_uri"].startswith("gs://"):
            raise ValueError("only staged GCS audio accepted")
        with self._tx() as db:
            self._verify_claim(db, claim)
            db.execute(
                "INSERT OR IGNORE INTO v2_windows(job_id,stage_name,identity_digest,source_sha)"
                " VALUES(?,?,?,?)",
                (claim["job_id"],claim["stage"],_digest(window),claim["source_sha"]),
            )

    def _verify_claim(self, db: sqlite3.Connection, claim: dict[str, Any]) -> None:
        stage = db.execute(
            "SELECT * FROM v2_stages WHERE job_id=? AND name=?",
            (claim["job_id"], claim["stage"]),
        ).fetchone()
        job = self._job(db, claim["job_id"])
        if (stage is None or stage["state"] != "RUNNING"
                or stage["claim_hash"] != hashlib.sha256(claim["claim"].encode()).hexdigest()
                or job["status"] != "PROCESSING" or job["source_sha"] != claim["source_sha"]):
            raise V2Conflict("unrecognized/stale stage claim")

    def provider_approval_validator(self, claim: dict[str, Any]):
        """Validate adapter token, source, model AND registered GCS window."""
        def validate(token: str, source_sha: str, model: str, window: dict[str, Any]) -> bool:
            try:
                with self._tx() as db:
                    self._verify_claim(db, claim)
                    if (token != claim["claim"] or source_sha != claim["source_sha"]
                            or MODEL_BY_STAGE.get(claim["stage"]) != model):
                        return False
                    binding = db.execute(
                        "SELECT 1 FROM v2_windows WHERE job_id=? AND stage_name=? "
                        "AND identity_digest=? AND source_sha=?",
                        (claim["job_id"],claim["stage"],_digest(window),source_sha),
                    ).fetchone()
                    return bool(binding)
            except (V2Conflict, KeyError, TypeError):
                return False
        return validate

    def complete(self, claim: dict[str, Any], *, evidence_sha: str,
                 actual_cents: int, comparison_has_dispute: bool | None = None) -> None:
        if len(evidence_sha) != 64 or any(c not in "0123456789abcdef" for c in evidence_sha):
            raise ValueError("missing verified evidence digest")
        if type(actual_cents) is not int or actual_cents < 0:
            raise ValueError("invalid actual cost")
        with self._tx() as db:
            self._verify_claim(db, claim)
            row = db.execute(
                "SELECT * FROM v2_stages WHERE job_id=? AND name=?",
                (claim["job_id"],claim["stage"]),
            ).fetchone()
            if claim["stage"] == "compare" and comparison_has_dispute is None:
                raise V2Conflict("comparison must persist disagreement finding")
            if claim["stage"] != "compare" and comparison_has_dispute is not None:
                raise V2Conflict("only comparison may set dispute status")
            if actual_cents > row["estimate_cents"]:
                # An already-billed overrun must be reconciled by owner, never
                # quietly discard its true expense or authorize later stages.
                raise V2Conflict("actual provider cost above reserved estimate")
            db.execute(
                "UPDATE v2_stages SET state='COMPLETE',actual_cents=?,evidence_sha=?,"
                "review_required=?,claim_hash=NULL,updated_at=? WHERE job_id=? AND name=?",
                (actual_cents, evidence_sha,
                 int(comparison_has_dispute) if comparison_has_dispute is not None else None,
                 _now(), claim["job_id"],claim["stage"]),
            )
            # Final-stage completion must persist the review boundary inside
            # this transaction. claim_next() raises when nothing remains, so
            # any status write there would roll back with that exception.
            if claim["stage"] == "golden_qa":
                db.execute(
                    "UPDATE v2_jobs SET status='AWAITING_FINAL_REVIEW',"
                    "revision=revision+1 WHERE id=?",
                    (claim["job_id"],),
                )

    def hold_for_review(self, claim: dict[str, Any], *, evidence_sha: str) -> None:
        """Known semantic/structural issue: persist evidence, block next stages."""
        if len(evidence_sha) != 64 or any(c not in "0123456789abcdef" for c in evidence_sha):
            raise ValueError("review evidence SHA256 required")
        with self._tx() as db:
            self._verify_claim(db, claim)
            db.execute(
                "UPDATE v2_stages SET state='REVIEW_REQUIRED',evidence_sha=?,"
                "claim_hash=NULL,updated_at=? WHERE job_id=? AND name=?",
                (evidence_sha, _now(), claim["job_id"], claim["stage"]),
            )
            db.execute("UPDATE v2_jobs SET status='NEEDS_REVIEW' WHERE id=?", (claim["job_id"],))

    def mark_outcome_unknown(self, claim: dict[str, Any]) -> None:
        with self._tx() as db:
            self._verify_claim(db, claim)
            db.execute(
                "UPDATE v2_stages SET state='OUTCOME_UNKNOWN',updated_at=? "
                "WHERE job_id=? AND name=?", (_now(), claim["job_id"], claim["stage"]),
            )
            db.execute(
                "UPDATE v2_jobs SET status='NEEDS_RECONCILIATION' WHERE id=?", (claim["job_id"],),
            )

    def reconcile_unknown(
        self, job_id: str, *, stage: str, actor: str, evidence_sha: str,
        actual_cents: int, evidence_verifier: Any,
    ) -> None:
        """Resume only when trusted code independently verifies completed output.

        A verified immutable provider response/operation AND source/window
        identity must be checked by evidence_verifier. No external provider
        requests occur here; absence of real evidence remains blocked.
        """
        if (stage not in MODEL_BY_STAGE or not actor.strip()
                or len(evidence_sha) != 64
                or any(ch not in "0123456789abcdef" for ch in evidence_sha)
                or type(actual_cents) is not int or actual_cents < 0):
            raise ValueError("invalid reconciliation proof")
        with self._tx() as db:
            job = self._job(db, job_id)
            row = db.execute(
                "SELECT * FROM v2_stages WHERE job_id=? AND name=?", (job_id, stage),
            ).fetchone()
            if (job["status"] != "NEEDS_RECONCILIATION" or row is None
                    or row["state"] != "OUTCOME_UNKNOWN"
                    or actual_cents > row["estimate_cents"]):
                raise V2Conflict("no eligible unknown provider outcome")
            # External verifier must validate RAW provider evidence and exact
            # staged source identity; a self-declared proof is never enough.
            if not callable(evidence_verifier) or not evidence_verifier(
                job_id=job_id, stage=stage, source_sha=job["source_sha"],
                evidence_sha=evidence_sha,
            ):
                raise V2Conflict("provider evidence not independently verified")
            other_spent = sum(
                int(item["actual_cents"] or 0) for item in self._stages(db, job_id)
            )
            if other_spent + actual_cents > job["approved_cents"]:
                raise V2Conflict("reconciliation would exceed approved budget")
            db.execute(
                "UPDATE v2_stages SET state='COMPLETE',actual_cents=?,"
                "evidence_sha=?,claim_hash=NULL,updated_at=? WHERE job_id=? AND name=?",
                (actual_cents,evidence_sha,_now(),job_id,stage),
            )
            db.execute(
                "INSERT INTO v2_reconciliations(job_id,stage_name,actor,"
                "evidence_sha,actual_cents,verified_at) VALUES(?,?,?,?,?,?)",
                (job_id,stage,actor,evidence_sha,actual_cents,_now()),
            )
            db.execute(
                "UPDATE v2_jobs SET status='PROCESSING',revision=revision+1 WHERE id=?",
                (job_id,),
            )

    def status(self, job_id: str) -> dict[str, Any]:
        with self._tx() as db:
            job = dict(self._job(db, job_id))
            rows = [dict(s) for s in self._stages(db, job_id)]
            job.pop("approval_reference", None)
            for row in rows:
                row.pop("claim_hash", None)
            job["stages"] = rows
            job["spent_cents"] = sum(row["actual_cents"] or 0 for row in rows)
            return job
