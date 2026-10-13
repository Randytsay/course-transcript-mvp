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
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STAGES = (
    "gemini_primary", "chirp_independent", "compare",
    "third_optional", "adjudication", "golden_qa",
)
RESTARTABLE_LOCAL_STAGES = frozenset({"compare", "adjudication", "golden_qa"})
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


def approved_trial_exposure(db: sqlite3.Connection) -> int:
    """Lifetime observed charges plus conservative remaining approved exposure.

    Must run inside the caller's active SQLite transaction. For a pending or
    uncertain paid stage reserve at least its full estimate; for completed
    stages count the independently recorded actual, including overruns.
    This is a local test-cap gate, not a replacement for provider billing.
    """
    total = 0
    for item in db.execute(
        "SELECT s.state,s.estimate_cents,s.actual_cents "
        "FROM v2_stages s JOIN v2_jobs j ON s.job_id=j.id "
        "WHERE j.approved_cents IS NOT NULL"
    ):
        estimate = int(item[1])
        actual = item[2]
        total += (
            int(actual) if item[0] in {"COMPLETE", "SKIPPED"}
            and actual is not None else
            max(estimate, int(actual) if actual is not None else 0)
        )
    return total


class GoldenV2Ledger:
    def __init__(self, database: Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @classmethod
    def open_existing(cls, database: Path) -> "GoldenV2Ledger":
        """Bind to an initialized ledger without creating or migrating it."""
        path = Path(database)
        if not path.is_file():
            raise FileNotFoundError("Golden V2 ledger does not exist")
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        if not {"v2_jobs", "v2_stages", "v2_chirp_operations"}.issubset(tables):
            raise sqlite3.OperationalError("Golden V2 ledger schema is incomplete")
        ledger = cls.__new__(cls)
        ledger.database = path
        return ledger

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database, timeout=15, isolation_level=None)) as db:
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
                CREATE TABLE IF NOT EXISTS v2_chirp_operations (
                    job_id TEXT NOT NULL, window_id TEXT NOT NULL,
                    source_sha TEXT NOT NULL, clip_sha TEXT NOT NULL,
                    gcs_uri TEXT NOT NULL, start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL, status TEXT NOT NULL,
                    operation_name TEXT, request_id TEXT NOT NULL,
                    output_uri TEXT NOT NULL DEFAULT '',
                    estimated_cents INTEGER NOT NULL DEFAULT 0,
                    purpose TEXT NOT NULL DEFAULT 'BASE',
                    assessment_sha TEXT,
                    target_intervals_json TEXT NOT NULL DEFAULT '[]',
                    result_sha TEXT, actual_cents INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(job_id,window_id),
                    FOREIGN KEY(job_id) REFERENCES v2_jobs(id)
                );
                CREATE TABLE IF NOT EXISTS v2_reconciliations (
                    job_id TEXT NOT NULL, stage_name TEXT NOT NULL,
                    actor TEXT NOT NULL, evidence_sha TEXT NOT NULL,
                    actual_cents INTEGER NOT NULL, verified_at TEXT NOT NULL,
                    PRIMARY KEY(job_id,stage_name,evidence_sha)
                );
                CREATE TABLE IF NOT EXISTS v2_review_resumptions (
                    job_id TEXT NOT NULL, stage_name TEXT NOT NULL,
                    prior_evidence_sha TEXT NOT NULL, reviewer TEXT NOT NULL,
                    review_reference TEXT NOT NULL, from_revision INTEGER NOT NULL,
                    resumed_at TEXT NOT NULL,
                    PRIMARY KEY(job_id,stage_name,from_revision)
                );
                CREATE TABLE IF NOT EXISTS v2_chirp_repair_approvals (
                    job_id TEXT NOT NULL, plan_sha TEXT NOT NULL,
                    assessment_sha TEXT NOT NULL, plan_json TEXT NOT NULL,
                    approved_cents INTEGER NOT NULL, reference TEXT NOT NULL,
                    actor TEXT NOT NULL, consumed_at TEXT,
                    PRIMARY KEY(job_id,plan_sha),
                    FOREIGN KEY(job_id) REFERENCES v2_jobs(id)
                );
            """)
            # Existing isolated V2 test databases may predate per-window
            # reservations. Migrate additively without touching V1 tables.
            cols = {r["name"] for r in db.execute("PRAGMA table_info(v2_chirp_operations)")}
            for name, definition in (("output_uri", "TEXT NOT NULL DEFAULT ''"),
                                     ("estimated_cents", "INTEGER NOT NULL DEFAULT 0"),
                                     ("purpose", "TEXT NOT NULL DEFAULT 'BASE'"),
                                     ("assessment_sha", "TEXT"),
                                     ("target_intervals_json", "TEXT NOT NULL DEFAULT '[]'")):
                if name not in cols:
                    db.execute(f"ALTER TABLE v2_chirp_operations ADD COLUMN {name} {definition}")

    def create(self, *, source_sha: str, mode: str,
               estimated_stage_cents: dict[str, int]) -> str:
        self._validate_new_job(source_sha, mode, estimated_stage_cents)
        job_id = "v2_" + uuid.uuid4().hex
        self._insert_job(job_id, source_sha, mode, estimated_stage_cents)
        return job_id

    def create_idempotent(self, *, source_sha: str, mode: str,
                          estimated_stage_cents: dict[str, int]) -> str:
        """Create/replay an owner registration by its exact immutable request."""
        self._validate_new_job(source_sha, mode, estimated_stage_cents)
        identity = _digest({
            "source_sha": source_sha,
            "mode": mode,
            "estimated_stage_cents": {
                name: estimated_stage_cents[name] for name in STAGES
            },
        })
        job_id = "v2_" + uuid.uuid5(
            uuid.NAMESPACE_URL, "golden-v2-owner-registration:" + identity,
        ).hex
        return self._create_or_replay(
            job_id=job_id, source_sha=source_sha, mode=mode,
            estimated_stage_cents=estimated_stage_cents,
        )

    @staticmethod
    def preflight_job_id(registration_sha256: str) -> str:
        """Stable local job identity for an immutable source-preflight package."""
        if (not isinstance(registration_sha256, str) or len(registration_sha256) != 64
                or any(x not in "0123456789abcdef" for x in registration_sha256)):
            raise ValueError("requires registration SHA256")
        return "v2p_" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            "golden-v2-source-preflight:" + registration_sha256,
        ).hex

    def create_from_preflight(self, *, source_sha: str, mode: str,
                              estimated_stage_cents: dict[str, int],
                              registration_sha256: str) -> str:
        """Idempotently register a verified local preflight, still unapproved.

        The registration digest is derived from a source-preflight manifest
        that binds the original bytes, normalized bytes, caller-supplied Drive
        observation, mode, and estimates. Replays return the same job only when
        every persisted source/mode/estimate value still agrees.
        """
        self._validate_new_job(source_sha, mode, estimated_stage_cents)
        job_id = self.preflight_job_id(registration_sha256)
        return self._create_or_replay(
            job_id=job_id, source_sha=source_sha, mode=mode,
            estimated_stage_cents=estimated_stage_cents,
        )

    def _create_or_replay(self, *, job_id: str, source_sha: str, mode: str,
                          estimated_stage_cents: dict[str, int]) -> str:
        with self._tx() as db:
            existing = db.execute(
                "SELECT source_sha,mode,estimated_cents FROM v2_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if existing is not None:
                stages = {
                    row["name"]: row["estimate_cents"]
                    for row in self._stages(db, job_id)
                }
                if (existing["source_sha"] != source_sha
                        or existing["mode"] != mode
                        or existing["estimated_cents"] != sum(estimated_stage_cents.values())
                        or stages != estimated_stage_cents):
                    raise V2Conflict("registration identity conflicts with ledger")
                return job_id
            db.execute(
                "INSERT INTO v2_jobs(id,source_sha,mode,estimated_cents,created_at) VALUES(?,?,?,?,?)",
                (job_id, source_sha, mode, sum(estimated_stage_cents.values()), _now()),
            )
            db.executemany(
                "INSERT INTO v2_stages(job_id,ord,name,estimate_cents) VALUES(?,?,?,?)",
                [(job_id, i, name, estimated_stage_cents[name]) for i, name in enumerate(STAGES)],
            )
        return job_id

    @staticmethod
    def _validate_new_job(source_sha: str, mode: str,
                          estimated_stage_cents: dict[str, int]) -> None:
        if len(source_sha) != 64 or any(x not in "0123456789abcdef" for x in source_sha):
            raise ValueError("requires source SHA256")
        if mode not in MODES or set(estimated_stage_cents) != set(STAGES):
            raise ValueError("invalid V2 mode or stage estimates")
        if any(type(v) is not int or v < 0 for v in estimated_stage_cents.values()):
            raise ValueError("invalid integer USD-cent estimate")

    def _insert_job(self, job_id: str, source_sha: str, mode: str,
                    estimated_stage_cents: dict[str, int]) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO v2_jobs(id,source_sha,mode,estimated_cents,created_at) VALUES(?,?,?,?,?)",
                (job_id, source_sha, mode, sum(estimated_stage_cents.values()), _now()),
            )
            db.executemany(
                "INSERT INTO v2_stages(job_id,ord,name,estimate_cents) VALUES(?,?,?,?)",
                [(job_id, i, name, estimated_stage_cents[name]) for i, name in enumerate(STAGES)],
            )

    def approve(self, job_id: str, *, revision: int, approved_cents: int,
                reference: str, actor: str, repeat_source_confirmed: bool = False) -> None:
        """Owner-authenticated endpoint should call this only after review.

        A caller cannot assert approval through provider adapters. Account
        authorization and real financial controls are external dependencies.
        """
        if (type(approved_cents) is not int or approved_cents < 0
                or type(repeat_source_confirmed) is not bool
                or not reference.strip() or not actor.strip()):
            raise ValueError("invalid owner approval")
        with self._tx() as db:
            job = self._job(db, job_id)
            if (job["revision"] != revision or job["status"] != "AWAITING_APPROVAL"
                    or approved_cents < job["estimated_cents"]):
                raise V2Conflict("approval revision, state, or amount mismatch")
            prior_approval = db.execute(
                "SELECT id FROM v2_jobs WHERE source_sha=? AND id<>? "
                "AND approved_cents IS NOT NULL LIMIT 1",
                (job["source_sha"], job_id),
            ).fetchone()
            if prior_approval is not None and not repeat_source_confirmed:
                raise V2Conflict(
                    "Source SHA has an earlier approved job; explicit "
                    "repeat_source_confirmed=true is required before another budget approval"
                )
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

    def _record_budget_overrun(self, db: sqlite3.Connection, *, job_id: str,
                               stage: str, actual_cents: int, evidence_sha: str) -> None:
        """Account for verified charges even when approval cannot cover them.

        The job remains frozen: recording a provider's already-incurred charge
        never authorizes another paid call or implicitly increases approval.
        """
        db.execute(
            "UPDATE v2_stages SET state='OVER_BUDGET',actual_cents=?,"
            "evidence_sha=?,claim_hash=NULL,updated_at=? WHERE job_id=? AND name=?",
            (actual_cents, evidence_sha, _now(), job_id, stage),
        )
        db.execute("UPDATE v2_jobs SET status='NEEDS_RECONCILIATION',"
                   "revision=revision+1 WHERE id=?", (job_id,))

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

    def claim_next(self, job_id: str, *, worker_id: str,
                   expected_stage: str | None = None,
                   expected_revision: int | None = None,
                   expected_source_sha: str | None = None,
                   aggregate_cap_cents: int | None = None) -> dict[str, Any]:
        """Claim only the next stage, never re-claim RUNNING or unknown work."""
        if not worker_id.strip():
            raise ValueError("worker identity required")
        if expected_stage is not None and expected_stage not in STAGES:
            raise ValueError("unknown expected stage")
        if expected_revision is not None and (
                type(expected_revision) is not int or expected_revision < 0):
            raise ValueError("invalid expected job revision")
        if expected_source_sha is not None and (
                not isinstance(expected_source_sha, str)
                or len(expected_source_sha) != 64
                or any(c not in "0123456789abcdef" for c in expected_source_sha)):
            raise ValueError("invalid expected normalized source SHA")
        if aggregate_cap_cents is not None and (
                type(aggregate_cap_cents) is not int
                or not 1 <= aggregate_cap_cents <= 100000):
            raise ValueError("invalid aggregate budget cap")
        with self._tx() as db:
            job = self._job(db, job_id)
            if (expected_revision is not None
                    and job["revision"] != expected_revision):
                raise V2Conflict("paid stage job revision changed since readiness review")
            if (expected_source_sha is not None
                    and job["source_sha"] != expected_source_sha):
                raise V2Conflict("paid stage source SHA changed since readiness review")
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
                if expected_stage is not None and row["name"] != expected_stage:
                    raise V2Conflict(
                        f"next stage is {row['name']}, expected {expected_stage}"
                    )
                if aggregate_cap_cents is not None and row["name"] in {
                        "gemini_primary", "chirp_independent", "third_optional"}:
                    # Serialize the aggregate decision together with this
                    # stage claim under BEGIN IMMEDIATE. Preserve already
                    # observed charges (even when above estimates), and
                    # conservatively reserve every unfinished approved stage.
                    if db.execute(
                        "SELECT 1 FROM v2_stages s "
                        "JOIN v2_jobs j ON s.job_id=j.id "
                        "WHERE j.approved_cents IS NOT NULL "
                        "AND s.name IN ('gemini_primary','chirp_independent','third_optional') "
                        "AND s.state IN ('RUNNING','OUTCOME_UNKNOWN','OVER_BUDGET') "
                        "LIMIT 1"
                    ).fetchone():
                        raise V2Conflict(
                            "unreconciled global paid stage blocks further trial charges"
                        )
                    if approved_trial_exposure(db) > aggregate_cap_cents:
                        raise V2Conflict(
                            "aggregate approved V2 exposure exceeds trial cap"
                        )
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

    def reclaim_stale_local(self, job_id: str, *, stage: str,
                            worker_id: str, minimum_age_seconds: int = 300,
                            expected_revision: int) -> dict[str, Any]:
        """Reclaim a crashed local-only stage; paid stages are never reclaimable.

        A CAS revision and age guard protect live workers; rotating the claim
        immediately invalidates the old worker's token. The caller must replay
        provider-free stage code against independently checked input evidence.
        """
        if (stage not in RESTARTABLE_LOCAL_STAGES or not worker_id.strip()
                or type(minimum_age_seconds) is not int or minimum_age_seconds < 0
                or type(expected_revision) is not int):
            raise V2Conflict("only stale, revision-bound local stages can be reclaimed")
        with self._tx() as db:
            job = self._job(db, job_id)
            rows = self._stages(db, job_id)
            target = next((row for row in rows if row["name"] == stage), None)
            if (job["status"] != "PROCESSING" or job["revision"] != expected_revision
                    or target is None or target["state"] != "RUNNING"):
                raise V2Conflict("local recovery revision, job or stage changed")
            if any(row["state"] not in {"COMPLETE", "SKIPPED"}
                   for row in rows if row["ord"] < target["ord"]):
                raise V2Conflict("preceding stage still unresolved")
            try:
                timestamp = datetime.fromisoformat(target["updated_at"])
                age = (datetime.now(timezone.utc) - timestamp).total_seconds()
            except (TypeError, ValueError) as exc:
                raise V2Conflict("missing verified stage heartbeat") from exc
            if age < minimum_age_seconds:
                raise V2Conflict("local stage is still inside its recovery grace")
            token = secrets.token_urlsafe(32)
            db.execute("UPDATE v2_stages SET claim_hash=?, updated_at=? "
                       "WHERE job_id=? AND name=?",
                       (hashlib.sha256(token.encode()).hexdigest(), _now(), job_id, stage))
            db.execute("UPDATE v2_jobs SET revision=revision+1 WHERE id=?", (job_id,))
            return {"job_id": job_id, "stage": stage, "claim": token,
                    "source_sha": job["source_sha"],
                    "estimate_cents": target["estimate_cents"]}

    def assert_upload_authorized(
        self, claim: dict[str, Any], *, stage: str, source_sha: str,
    ) -> None:
        """Confirm a verified live claim before potentially billable GCS IO.

        Caller must separately verify the normalized audio SHA and the
        private GCS destination policy. This gate prevents any cloud upload
        from unapproved, expired or wrong-model stage claims.
        """
        if stage not in {"gemini_primary", "chirp_independent", "third_optional"}:
            raise V2Conflict("only approved audio stages may upload windows")
        with self._tx() as db:
            self._verify_claim(db, claim)
            if claim["stage"] != stage or claim["source_sha"] != source_sha:
                raise V2Conflict("source or stage does not match cost approval")
            row = db.execute(
                "SELECT estimate_cents FROM v2_stages WHERE job_id=? AND name=?",
                (claim["job_id"], stage),
            ).fetchone()
            if row is None:
                raise V2Conflict("no stage estimate approved")

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
                self._record_budget_overrun(
                    db, job_id=claim["job_id"], stage=claim["stage"],
                    actual_cents=actual_cents, evidence_sha=evidence_sha,
                )
                return
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

    def hold_for_review(
        self, claim: dict[str, Any], *, evidence_sha: str,
        actual_cents: int = 0,
    ) -> None:
        """Known outcome with unresolved content: retain actual billed cost."""
        if len(evidence_sha) != 64 or any(c not in "0123456789abcdef" for c in evidence_sha):
            raise ValueError("review evidence SHA256 required")
        if type(actual_cents) is not int or actual_cents < 0:
            raise ValueError("invalid actual cost")
        with self._tx() as db:
            self._verify_claim(db, claim)
            stage = db.execute(
                "SELECT estimate_cents FROM v2_stages WHERE job_id=? AND name=?",
                (claim["job_id"], claim["stage"]),
            ).fetchone()
            if stage is None:
                raise V2Conflict("missing stage for review")
            if actual_cents > int(stage["estimate_cents"]):
                self._record_budget_overrun(
                    db, job_id=claim["job_id"], stage=claim["stage"],
                    actual_cents=actual_cents, evidence_sha=evidence_sha,
                )
                return
            db.execute(
                "UPDATE v2_stages SET state='REVIEW_REQUIRED',evidence_sha=?,"
                "actual_cents=?,claim_hash=NULL,updated_at=? WHERE job_id=? AND name=?",
                (evidence_sha, actual_cents, _now(), claim["job_id"], claim["stage"]),
            )
            db.execute("UPDATE v2_jobs SET status='NEEDS_REVIEW' WHERE id=?", (claim["job_id"],))

    def resume_adjudication_review(
        self, job_id: str, *, revision: int, reviewer: str,
        review_reference: str, previous_evidence_sha: str,
    ) -> None:
        """Explicitly resume a *local* disputed adjudication; never re-run ASR.

        A reviewer must inspect the previous immutable review artifact and
        supply its SHA. Only the adjudication stage can be reopened; the
        previously paid recognition stages and their charges stay immutable.
        The caller is responsible for authenticating the reviewer.
        """
        if (not reviewer.strip() or not review_reference.strip()
                or len(previous_evidence_sha) != 64 or any(
                    c not in "0123456789abcdef" for c in previous_evidence_sha)):
            raise ValueError("auditable review identity/reference/SHA required")
        with self._tx() as db:
            job = self._job(db, job_id)
            stage = db.execute(
                "SELECT * FROM v2_stages WHERE job_id=? AND name='adjudication'",
                (job_id,),
            ).fetchone()
            if (job["revision"] != revision or job["status"] != "NEEDS_REVIEW"
                    or stage is None or stage["state"] != "REVIEW_REQUIRED"
                    or stage["evidence_sha"] != previous_evidence_sha):
                raise V2Conflict("review revision/state/evidence differs from recorded hold")
            db.execute(
                "INSERT INTO v2_review_resumptions VALUES(?,?,?,?,?,?,?)",
                (job_id, "adjudication", previous_evidence_sha, reviewer,
                 review_reference, revision, _now()),
            )
            db.execute(
                "UPDATE v2_stages SET state='PENDING',evidence_sha=NULL,"
                "claim_hash=NULL,updated_at=? WHERE job_id=? AND name='adjudication'",
                (_now(), job_id),
            )
            db.execute(
                "UPDATE v2_jobs SET status='PROCESSING',revision=revision+1 WHERE id=?",
                (job_id,),
            )

    def complete_reviewed_primary(
        self, job_id: str, *, revision: int, reviewer: str,
        review_reference: str, previous_evidence_sha: str,
        reviewed_evidence_sha: str, evidence_verifier: Any,
    ) -> None:
        """Promote a reviewed overlap without re-claiming Gemini or changing cost.

        The caller must authenticate the reviewer and independently check the
        original raw windows, audio-seam decisions, and immutable reviewed file.
        CAS protects against stale approvals and concurrent resolution.
        """
        def sha(value: str) -> bool:
            return isinstance(value, str) and len(value) == 64 and all(
                char in "0123456789abcdef" for char in value)

        if (type(revision) is not int or not reviewer.strip() or
                not review_reference.strip() or not sha(previous_evidence_sha) or
                not sha(reviewed_evidence_sha) or
                reviewed_evidence_sha == previous_evidence_sha or
                not callable(evidence_verifier)):
            raise ValueError("independent, audit-bound primary review required")
        with self._tx() as db:
            job = self._job(db, job_id)
            stages = self._stages(db, job_id)
            first = stages[0]
            if (job["revision"] != revision or job["status"] != "NEEDS_REVIEW"
                    or first["name"] != "gemini_primary"
                    or first["state"] != "REVIEW_REQUIRED"
                    or first["evidence_sha"] != previous_evidence_sha
                    or any(stage["state"] != "PENDING" for stage in stages[1:])):
                raise V2Conflict("primary review revision/state/evidence mismatch")
            if not evidence_verifier(job_id=job_id, source_sha=job["source_sha"],
                                     old_sha=previous_evidence_sha,
                                     reviewed_sha=reviewed_evidence_sha):
                raise V2Conflict("primary review evidence verification failed")
            db.execute(
                "INSERT INTO v2_review_resumptions VALUES(?,?,?,?,?,?,?)",
                (job_id, "gemini_primary", previous_evidence_sha, reviewer,
                 review_reference, revision, _now()),
            )
            db.execute(
                "UPDATE v2_stages SET state='COMPLETE',evidence_sha=?,"
                "claim_hash=NULL,updated_at=? WHERE job_id=? AND name='gemini_primary'",
                (reviewed_evidence_sha, _now(), job_id),
            )
            db.execute(
                "UPDATE v2_jobs SET status='PROCESSING',revision=revision+1 WHERE id=?",
                (job_id,),
            )

    def mark_outcome_unknown(
        self, claim: dict[str, Any], *, known_cents: int = 0,
        evidence_sha: str | None = None,
    ) -> None:
        """Freeze ambiguous provider work while retaining already observed charges.

        Unknown windows may incur *additional* expense: known_cents is only
        a lower bound. Never release this stage or spend the remaining budget
        until an independent reconciliation has resolved it.
        """
        if type(known_cents) is not int or known_cents < 0:
            raise ValueError("invalid known billed cost")
        if evidence_sha is not None and (len(evidence_sha) != 64 or any(
                ch not in "0123456789abcdef" for ch in evidence_sha)):
            raise ValueError("invalid partial evidence digest")
        if known_cents and evidence_sha is None:
            raise ValueError("known cost requires auditable partial evidence")
        with self._tx() as db:
            self._verify_claim(db, claim)
            row = db.execute("SELECT actual_cents FROM v2_stages WHERE job_id=? AND name=?",
                             (claim["job_id"], claim["stage"])).fetchone()
            if int(row["actual_cents"] or 0) > known_cents:
                raise V2Conflict("cannot reduce already observed provider expense")
            db.execute(
                "UPDATE v2_stages SET state='OUTCOME_UNKNOWN',actual_cents=?,"
                "evidence_sha=?,updated_at=? WHERE job_id=? AND name=?",
                (known_cents, evidence_sha, _now(), claim["job_id"], claim["stage"]),
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
                    or row["state"] != "OUTCOME_UNKNOWN"):
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
                if item["name"] != stage
            )
            if actual_cents < int(row["actual_cents"] or 0):
                raise V2Conflict("reconciled charge less than immutable observed usage")
            if (actual_cents > row["estimate_cents"]
                    or other_spent + actual_cents > job["approved_cents"]):
                self._record_budget_overrun(
                    db, job_id=job_id, stage=stage,
                    actual_cents=actual_cents, evidence_sha=evidence_sha,
                )
                db.execute(
                    "INSERT INTO v2_reconciliations(job_id,stage_name,actor,"
                    "evidence_sha,actual_cents,verified_at) VALUES(?,?,?,?,?,?)",
                    (job_id, stage, actor, evidence_sha, actual_cents, _now()),
                )
                return
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

    def reconcile_unsubmitted_provider_call(
        self, job_id: str, *, stage: str, actor: str, evidence_sha: str,
        evidence_verifier: Any, attempt_archiver: Any,
        owner_authorization_reference: str | None = None,
    ) -> None:
        """Return a proven-never-submitted primary call to PENDING.

        This is narrower than normal provider reconciliation: only the Gemini
        primary stage with zero observed usage may be reopened, and trusted
        code must independently verify a complete, source-bound control-plane
        request-count window. The original raw attempt remains separately
        archived by the recovery command before this ledger transition.
        """
        if (stage != "gemini_primary" or not actor.strip()
                or (owner_authorization_reference is not None
                    and (not isinstance(owner_authorization_reference, str)
                         or not owner_authorization_reference.strip()
                         or len(owner_authorization_reference) > 240
                         or any(ord(ch) < 32 for ch in owner_authorization_reference)))
                or len(evidence_sha) != 64
                or any(ch not in "0123456789abcdef" for ch in evidence_sha)):
            raise ValueError("invalid no-dispatch reconciliation proof")
        with self._tx() as db:
            job = self._job(db, job_id)
            row = db.execute(
                "SELECT * FROM v2_stages WHERE job_id=? AND name=?", (job_id, stage),
            ).fetchone()
            if (job["status"] != "NEEDS_RECONCILIATION" or row is None
                    or row["state"] != "OUTCOME_UNKNOWN"
                    or int(row["actual_cents"] or 0) != 0):
                raise V2Conflict("no zero-usage unknown primary call to reopen")
            prior_reconciliations = db.execute(
                "SELECT COUNT(*) FROM v2_reconciliations WHERE job_id=? AND stage_name=?",
                (job_id, stage),
            ).fetchone()[0]
            if prior_reconciliations >= 2:
                raise V2Conflict("the bounded no-dispatch retry limit is exhausted")
            if prior_reconciliations == 1 and owner_authorization_reference is None:
                raise V2Conflict("explicit owner authorization is required for an additional retry")
            if prior_reconciliations == 0 and owner_authorization_reference is not None:
                raise V2Conflict("owner repeat authorization is only valid for an additional retry")
            if not callable(evidence_verifier) or not evidence_verifier(
                job_id=job_id, stage=stage, source_sha=job["source_sha"],
                created_at=job["created_at"], stage_updated_at=row["updated_at"],
                evidence_sha=evidence_sha,
            ):
                raise V2Conflict("complete no-dispatch evidence not independently verified")
            if not callable(attempt_archiver):
                raise V2Conflict("raw unknown attempt archiver is required")
            attempt_archiver()
            db.execute(
                "UPDATE v2_stages SET state='PENDING',actual_cents=0,claim_hash=NULL,"
                "updated_at=? WHERE job_id=? AND name=?",
                (_now(), job_id, stage),
            )
            if owner_authorization_reference is not None:
                actor = f"{actor}; owner_authorization={owner_authorization_reference}"
            db.execute(
                "INSERT INTO v2_reconciliations(job_id,stage_name,actor,"
                "evidence_sha,actual_cents,verified_at) VALUES(?,?,?,?,?,?)",
                (job_id, stage, actor, evidence_sha, 0, _now()),
            )
            db.execute(
                "UPDATE v2_jobs SET status='PROCESSING',revision=revision+1 WHERE id=?",
                (job_id,),
            )

    def assert_stage_claim(self, claim: dict[str, Any], *, stage: str) -> None:
        with self._tx() as db:
            self._verify_claim(db, claim)
            if claim["stage"] != stage:
                raise V2Conflict("incorrect V2 stage claim")

    def plan_chirp_window(self, claim: dict[str, Any], window: dict[str, Any]) -> None:
        """Bind input URI, SHA and exact offsets before provider submission."""
        try:
            window_id = window["window_id"]
            source_sha = window["source_sha"]
            clip_sha = window["clip_sha"]
            uri = window["gcs_uri"]
            start, end = window["start_ms"], window["end_ms"]
            output_uri = window.get("output_uri", "")
            estimated_cents = window.get("estimated_cents", 0)
        except KeyError as exc:
            raise V2Conflict("incomplete Chirp window") from exc
        if (not isinstance(window_id, str) or not window_id or len(window_id) > 128
                or source_sha != claim["source_sha"]
                or not isinstance(clip_sha, str) or len(clip_sha) != 64
                or any(c not in "0123456789abcdef" for c in clip_sha)
                or not isinstance(uri, str) or not uri.startswith("gs://")
                or not isinstance(output_uri, str)
                or (output_uri and (not output_uri.startswith("gs://") or not output_uri.endswith("/")))
                or (output_uri and output_uri.startswith(uri + "/"))
                or type(estimated_cents) is not int or estimated_cents < 0
                or type(start) is not int or type(end) is not int
                or start < 0 or end <= start or end - start > 20 * 60 * 1000):
            raise V2Conflict("Chirp window must match approved source, SHA, URI and duration")
        request_id = _digest({"job": claim["job_id"], **{
            key: window[key] for key in (
                "window_id", "source_sha", "clip_sha", "gcs_uri", "start_ms", "end_ms")},
                              "output_uri": output_uri, "estimated_cents": estimated_cents})
        with self._tx() as db:
            self._verify_claim(db, claim)
            if claim["stage"] != "chirp_independent":
                raise V2Conflict("Chirp stage claim required")
            previous = db.execute(
                "SELECT * FROM v2_chirp_operations WHERE job_id=? AND window_id=?",
                (claim["job_id"], window_id),
            ).fetchone()
            if previous is not None:
                if any(previous[key] != value for key, value in (
                    ("source_sha", source_sha), ("clip_sha", clip_sha),
                    ("gcs_uri", uri), ("start_ms", start), ("end_ms", end),
                    ("output_uri", output_uri), ("estimated_cents", estimated_cents),
                    ("request_id", request_id),
                )):
                    raise V2Conflict("existing Chirp window identity disagrees")
                return
            if db.execute(
                "SELECT 1 FROM v2_chirp_operations WHERE job_id=? AND status!='PLANNED' LIMIT 1",
                (claim["job_id"],),
            ).fetchone():
                raise V2Conflict("cannot extend Chirp window plan after first submission")
            stage = db.execute(
                "SELECT estimate_cents FROM v2_stages WHERE job_id=? AND name='chirp_independent'",
                (claim["job_id"],),
            ).fetchone()
            planned = db.execute(
                "SELECT COALESCE(SUM(estimated_cents),0) FROM v2_chirp_operations WHERE job_id=?",
                (claim["job_id"],),
            ).fetchone()[0]
            if planned + estimated_cents > stage["estimate_cents"]:
                raise V2Conflict("planned Chirp window estimates exceed stage approval")
            db.execute(
                "INSERT INTO v2_chirp_operations(job_id,window_id,source_sha,clip_sha,"
                "gcs_uri,start_ms,end_ms,status,request_id,output_uri,estimated_cents,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim["job_id"], window_id, source_sha, clip_sha, uri, start,
                 end, "PLANNED", request_id, output_uri, estimated_cents, _now()),
            )

    @staticmethod
    def _repair_plan_rows(windows: list[dict[str, Any]], source_sha: str) -> list[dict[str, Any]]:
        if not isinstance(windows, list) or not windows or len(windows) > 12:
            raise V2Conflict("bounded Standard repair plan must contain 1-12 windows")
        normalized: list[dict[str, Any]] = []
        ids: set[str] = set()
        for row in windows:
            if not isinstance(row, dict):
                raise V2Conflict("invalid Standard repair window")
            try:
                window_id, clip_sha = row["window_id"], row["clip_sha"]
                uri, output = row["gcs_uri"], row["output_uri"]
                start, end, cents = row["start_ms"], row["end_ms"], row["estimated_cents"]
                assessment_sha = row["assessment_sha"]
                targets = row["target_intervals"]
            except KeyError as exc:
                raise V2Conflict("incomplete Standard repair identity") from exc
            if (not isinstance(window_id, str) or not window_id.startswith("repair-")
                    or len(window_id) > 128 or window_id in ids
                    or row.get("source_sha") != source_sha
                    or not isinstance(clip_sha, str) or len(clip_sha) != 64
                    or any(c not in "0123456789abcdef" for c in clip_sha)
                    or not isinstance(assessment_sha, str) or len(assessment_sha) != 64
                    or any(c not in "0123456789abcdef" for c in assessment_sha)
                    or not isinstance(uri, str) or not uri.startswith("gs://")
                    or not isinstance(output, str) or not output.startswith("gs://")
                    or not output.endswith("/") or output.startswith(uri + "/")
                    or type(start) is not int or type(end) is not int
                    or start < 0 or end <= start or end - start > 300_000
                    or type(cents) is not int or cents < 0
                    or row.get("strategy") != "STANDARD_BATCH"
                    or not isinstance(targets, list) or not targets):
                raise V2Conflict("invalid bounded Standard repair window")
            target_rows = []
            for target in targets:
                if (not isinstance(target, dict)
                        or type(target.get("start_ms")) is not int
                        or type(target.get("end_ms")) is not int
                        or not start <= target["start_ms"] < target["end_ms"] <= end):
                    raise V2Conflict("repair target must be inside its source clip")
                target_rows.append({"start_ms": target["start_ms"],
                                    "end_ms": target["end_ms"]})
            ids.add(window_id)
            normalized.append({
                "window_id": window_id, "source_sha": source_sha,
                "clip_sha": clip_sha, "gcs_uri": uri,
                "start_ms": start, "end_ms": end, "output_uri": output,
                "estimated_cents": cents, "assessment_sha": assessment_sha,
                "target_intervals": target_rows,
                "strategy": "STANDARD_BATCH",
            })
        return normalized

    def approve_chirp_repair_plan(
        self, job_id: str, *, expected_revision: int, assessment_sha: str,
        windows: list[dict[str, Any]], approved_cents: int,
        reference: str, actor: str,
    ) -> dict[str, Any]:
        """Persist explicit, plan-bound owner approval for bounded Standard repair.

        Approval is limited to the exact source ranges, clip/object identities,
        assessment evidence and cent ceiling. It cannot widen the job or stage
        budget, and it is consumed once when the immutable plan is committed.
        """
        if (not isinstance(assessment_sha, str) or len(assessment_sha) != 64
                or any(c not in "0123456789abcdef" for c in assessment_sha)
                or type(approved_cents) is not int or approved_cents < 0
                or not isinstance(reference, str) or not reference.strip()
                or not isinstance(actor, str) or not actor.strip()):
            raise V2Conflict("repair approval requires reviewer, reference, evidence and cost ceiling")
        with self._tx() as db:
            job = self._job(db, job_id)
            stage = db.execute("SELECT * FROM v2_stages WHERE job_id=? "
                               "AND name='chirp_independent'", (job_id,)).fetchone()
            if (job["revision"] != expected_revision or job["status"] != "PROCESSING"
                    or stage is None or stage["state"] != "RUNNING"):
                raise V2Conflict("repair approval revision, job or Chirp stage changed")
            existing = db.execute("SELECT * FROM v2_chirp_operations WHERE job_id=?",
                                  (job_id,)).fetchall()
            if (not existing or any(row["status"] != "DONE" for row in existing)):
                raise V2Conflict("repair approval requires all prior Chirp operations complete")
            rows = self._repair_plan_rows(windows, job["source_sha"])
            if any(row["assessment_sha"] != assessment_sha for row in rows):
                raise V2Conflict("repair plan does not match acoustic assessment")
            estimate = sum(row["estimated_cents"] for row in rows)
            chirp_actual = sum(int(row["actual_cents"] or 0) for row in existing)
            other_actual = sum(int(row["actual_cents"] or 0) for row in self._stages(db, job_id)
                               if row["name"] != "chirp_independent")
            available = min(int(stage["estimate_cents"]) - chirp_actual,
                            int(job["approved_cents"] or 0) - other_actual - chirp_actual)
            if (estimate <= 0 or estimate > approved_cents
                    or approved_cents > available):
                raise V2Conflict("repair cost approval exceeds available preapproved budget")
            plan_json = json.dumps({"assessment_sha": assessment_sha, "windows": rows,
                                    "approved_cents": approved_cents},
                                   ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            plan_sha = _digest(json.loads(plan_json))
            prior = db.execute("SELECT * FROM v2_chirp_repair_approvals WHERE job_id=? AND plan_sha=?",
                               (job_id, plan_sha)).fetchone()
            if prior is not None:
                if (prior["plan_json"] != plan_json or prior["reference"] != reference
                        or prior["actor"] != actor or prior["approved_cents"] != approved_cents):
                    raise V2Conflict("conflicting approval for existing repair plan")
            else:
                db.execute("INSERT INTO v2_chirp_repair_approvals(job_id,plan_sha,assessment_sha,"
                           "plan_json,approved_cents,reference,actor) VALUES(?,?,?,?,?,?,?)",
                           (job_id, plan_sha, assessment_sha, plan_json, approved_cents,
                            reference.strip(), actor.strip()))
            return {"job_id": job_id, "plan_sha": plan_sha,
                    "approved_cents": approved_cents, "window_count": len(rows)}

    def plan_chirp_repair_windows(
        self, claim: dict[str, Any], *, plan_sha: str,
        windows: list[dict[str, Any]],
    ) -> None:
        """Commit only the exact repair windows explicitly approved above."""
        if (not isinstance(plan_sha, str) or len(plan_sha) != 64
                or any(c not in "0123456789abcdef" for c in plan_sha)):
            raise V2Conflict("invalid approved Standard repair plan identity")
        with self._tx() as db:
            self._verify_claim(db, claim)
            if claim["stage"] != "chirp_independent":
                raise V2Conflict("active Chirp claim required for repair plan")
            job = self._job(db, claim["job_id"])
            stage = db.execute("SELECT * FROM v2_stages WHERE job_id=? "
                               "AND name='chirp_independent'", (claim["job_id"],)).fetchone()
            approval = db.execute("SELECT * FROM v2_chirp_repair_approvals "
                                  "WHERE job_id=? AND plan_sha=?",
                                  (claim["job_id"], plan_sha)).fetchone()
            if approval is None or approval["consumed_at"] is not None:
                raise V2Conflict("owner-approved Standard repair plan is missing or already used")
            existing = db.execute("SELECT * FROM v2_chirp_operations WHERE job_id=?",
                                  (claim["job_id"],)).fetchall()
            if not existing or any(row["status"] != "DONE" for row in existing):
                raise V2Conflict("all prior Chirp operations must complete before repair")
            rows = self._repair_plan_rows(windows, job["source_sha"])
            approved = json.loads(approval["plan_json"])
            if (rows != approved["windows"] or approved["assessment_sha"] != approval["assessment_sha"]
                    or _digest(approved) != plan_sha):
                raise V2Conflict("repair windows differ from owner-approved plan")
            total = sum(row["estimated_cents"] for row in rows)
            if total > approval["approved_cents"]:
                raise V2Conflict("repair window estimates exceed owner-approved ceiling")
            actual = sum(int(row["actual_cents"] or 0) for row in existing)
            if actual + total > stage["estimate_cents"]:
                raise V2Conflict("repair estimates exceed remaining Chirp stage budget")
            other = sum(int(row["actual_cents"] or 0) for row in self._stages(db, claim["job_id"])
                        if row["name"] != "chirp_independent")
            if other + actual + total > int(job["approved_cents"] or 0):
                raise V2Conflict("repair estimates exceed remaining job approval")
            for row in rows:
                request_id = _digest({"job": claim["job_id"], **row})
                db.execute(
                    "INSERT INTO v2_chirp_operations(job_id,window_id,source_sha,clip_sha,"
                    "gcs_uri,start_ms,end_ms,status,request_id,output_uri,estimated_cents,"
                    "purpose,assessment_sha,target_intervals_json,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (claim["job_id"], row["window_id"], row["source_sha"], row["clip_sha"],
                     row["gcs_uri"], row["start_ms"], row["end_ms"], "PLANNED", request_id,
                     row["output_uri"], row["estimated_cents"], "REPAIR",
                     row["assessment_sha"], json.dumps(row["target_intervals"],
                         sort_keys=True, separators=(",", ":")), _now()),
                )
            db.execute("UPDATE v2_chirp_repair_approvals SET consumed_at=? "
                       "WHERE job_id=? AND plan_sha=?", (_now(), claim["job_id"], plan_sha))
            db.execute("UPDATE v2_stages SET updated_at=? WHERE job_id=? "
                       "AND name='chirp_independent'", (_now(), claim["job_id"]))

    def chirp_windows(self, job_id: str) -> list[dict[str, Any]]:
        with self._tx() as db:
            self._job(db, job_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM v2_chirp_operations WHERE job_id=? "
                "ORDER BY CASE purpose WHEN 'BASE' THEN 0 ELSE 1 END,start_ms,window_id",
                (job_id,),
            ).fetchall()]

    def begin_chirp_submit(self, claim: dict[str, Any], window_id: str) -> dict[str, Any]:
        """Durably mark request intent before network IO; never auto-resubmit."""
        with self._tx() as db:
            self._verify_claim(db, claim)
            if claim["stage"] != "chirp_independent":
                raise V2Conflict("only Chirp claim can submit recognition")
            row = db.execute("SELECT * FROM v2_chirp_operations WHERE job_id=? AND window_id=?",
                             (claim["job_id"], window_id)).fetchone()
            if row is None or row["status"] != "PLANNED":
                raise V2Conflict("Chirp input not planned or already attempted")
            # No new window may be introduced after any provider attempt: the
            # billable set was preflighted as one stage reservation.
            stage = db.execute("SELECT estimate_cents FROM v2_stages WHERE job_id=? "
                               "AND name='chirp_independent'", (claim["job_id"],)).fetchone()
            reserved = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN purpose='BASE' AND status='DONE' "
                "THEN actual_cents ELSE estimated_cents END),0) "
                "FROM v2_chirp_operations WHERE job_id=?", (claim["job_id"],),
            ).fetchone()[0]
            if reserved > stage["estimate_cents"]:
                raise V2Conflict("Chirp preflight reservation insufficient")
            db.execute("UPDATE v2_chirp_operations SET status='SUBMITTING',updated_at=? "
                       "WHERE job_id=? AND window_id=?", (_now(), claim["job_id"], window_id))
            return dict(row)

    def record_chirp_submission(self, claim: dict[str, Any], window_id: str,
                                operation_name: str) -> None:
        if not isinstance(operation_name, str) or not operation_name.strip():
            raise V2Conflict("durable Chirp operation name required")
        with self._tx() as db:
            self._verify_claim(db, claim)
            row = db.execute("SELECT status FROM v2_chirp_operations "
                             "WHERE job_id=? AND window_id=?", (claim["job_id"], window_id)).fetchone()
            if row is None or row["status"] != "SUBMITTING":
                raise V2Conflict("no matching pending Chirp submission intent")
            db.execute("UPDATE v2_chirp_operations SET status='SUBMITTED',"
                       "operation_name=?,updated_at=? WHERE job_id=? AND window_id=?",
                       (operation_name, _now(), claim["job_id"], window_id))
            db.execute("UPDATE v2_stages SET updated_at=? WHERE job_id=? "
                       "AND name='chirp_independent'",
                       (_now(), claim["job_id"]))

    def resume_chirp_submissions(
        self, job_id: str, *, worker_id: str, expected_revision: int,
        minimum_age_seconds: int = 300,
    ) -> dict[str, Any]:
        """Rotate the Chirp claim only when known submissions leave planned windows.

        This recovers a worker stopped between requests. A SUBMITTING row means
        the provider may have accepted an unrecorded request, so it remains
        frozen for manual reconciliation and can never be replayed here.
        """
        if (not worker_id.strip() or type(expected_revision) is not int
                or type(minimum_age_seconds) is not int or minimum_age_seconds < 0):
            raise V2Conflict("valid worker, revision and recovery age required")
        with self._tx() as db:
            job = self._job(db, job_id)
            stage = db.execute(
                "SELECT * FROM v2_stages WHERE job_id=? AND name='chirp_independent'",
                (job_id,),
            ).fetchone()
            rows = db.execute(
                "SELECT * FROM v2_chirp_operations WHERE job_id=? ORDER BY start_ms,window_id",
                (job_id,),
            ).fetchall()
            if (job["status"] != "PROCESSING" or job["revision"] != expected_revision
                    or stage is None or stage["state"] != "RUNNING" or not rows):
                raise V2Conflict("Chirp recovery revision, job or stage changed")
            states = {row["status"] for row in rows}
            if "SUBMITTING" in states:
                raise V2Conflict("ambiguous Chirp submission requires manual reconciliation")
            if "PLANNED" not in states or not states <= {"PLANNED", "SUBMITTED", "DONE"}:
                raise V2Conflict("no safely resumable planned Chirp windows")
            if any(row["status"] in {"SUBMITTED", "DONE"}
                   and not row["operation_name"] for row in rows):
                raise V2Conflict("known Chirp operation identity is missing")
            try:
                updated = datetime.fromisoformat(stage["updated_at"])
                age = (datetime.now(timezone.utc) - updated).total_seconds()
            except (TypeError, ValueError) as exc:
                raise V2Conflict("missing verified Chirp stage heartbeat") from exc
            if updated.tzinfo is None or age < minimum_age_seconds:
                raise V2Conflict("Chirp worker is still inside its recovery grace")
            token = secrets.token_urlsafe(32)
            db.execute(
                "UPDATE v2_stages SET claim_hash=?,updated_at=? WHERE job_id=? "
                "AND name='chirp_independent'",
                (hashlib.sha256(token.encode()).hexdigest(), _now(), job_id),
            )
            db.execute("UPDATE v2_jobs SET revision=revision+1 WHERE id=?", (job_id,))
            return {"job_id": job_id, "stage": "chirp_independent", "claim": token,
                    "source_sha": job["source_sha"],
                    "estimate_cents": stage["estimate_cents"]}

    def record_chirp_result(self, claim: dict[str, Any], window_id: str,
                            *, operation_name: str, evidence_sha: str,
                            actual_cents: int, verifier: Any) -> None:
        """Accept only independently verified raw operation/result and actual usage."""
        if (not isinstance(evidence_sha, str) or len(evidence_sha) != 64
                or any(c not in "0123456789abcdef" for c in evidence_sha)
                or type(actual_cents) is not int or actual_cents < 0):
            raise V2Conflict("verified result SHA and known actual usage required")
        self.assert_stage_claim(claim, stage="chirp_independent")
        self.recover_submitted_chirp(
            claim["job_id"], window_id, operation_name=operation_name,
            evidence_sha=evidence_sha, actual_cents=actual_cents, verifier=verifier,
        )

    def complete_chirp_operations(self, claim: dict[str, Any], *, evidence_sha: str,
                                  verifier: Any) -> None:
        self.assert_stage_claim(claim, stage="chirp_independent")
        self.finish_recovered_chirp(claim["job_id"], evidence_sha=evidence_sha,
                                    verifier=verifier)

    def recover_submitted_chirp(
        self, job_id: str, window_id: str, *, operation_name: str,
        evidence_sha: str, actual_cents: int, verifier: Any,
    ) -> None:
        """Recovery after process restart uses persisted operation ID, never submits.

        The trusted worker verifies the raw provider response/operation and
        source-bound window; API callers cannot supply their own verifier.
        """
        if (not isinstance(evidence_sha, str) or len(evidence_sha) != 64
                or any(c not in "0123456789abcdef" for c in evidence_sha)
                or type(actual_cents) is not int or actual_cents < 0):
            raise V2Conflict("missing verified recovery evidence and usage")
        with self._tx() as db:
            job = self._job(db, job_id)
            stage = db.execute("SELECT * FROM v2_stages WHERE job_id=? AND name='chirp_independent'",
                               (job_id,)).fetchone()
            row = db.execute("SELECT * FROM v2_chirp_operations WHERE job_id=? AND window_id=?",
                             (job_id, window_id)).fetchone()
            running = (job["status"] == "PROCESSING" and stage is not None
                       and stage["state"] == "RUNNING")
            frozen = (job["status"] == "NEEDS_RECONCILIATION" and stage is not None
                      and stage["state"] == "OVER_BUDGET")
            # A budget freeze blocks new POSTs, but already-submitted LROs
            # may complete and incur charges. Recover their immutable evidence.
            if (not (running or frozen) or job["approved_cents"] is None
                    or row is None or row["status"] not in {"SUBMITTED", "DONE"}
                    or not operation_name or row["operation_name"] != operation_name):
                raise V2Conflict("no recoverable submitted Chirp operation")
            if row["status"] == "DONE":
                if row["result_sha"] == evidence_sha and row["actual_cents"] == actual_cents:
                    return
                raise V2Conflict("conflicting Chirp recovery evidence")
            if not callable(verifier) or not verifier(
                job_id=job_id, window_id=window_id, operation_name=operation_name,
                source_sha=row["source_sha"], clip_sha=row["clip_sha"],
                gcs_uri=row["gcs_uri"], start_ms=row["start_ms"], end_ms=row["end_ms"],
                evidence_sha=evidence_sha,
            ):
                raise V2Conflict("independent Chirp evidence verification failed")
            other = db.execute(
                "SELECT COALESCE(SUM(actual_cents),0) FROM v2_chirp_operations WHERE job_id=?",
                (job_id,),
            ).fetchone()[0]
            committed = sum(int(s["actual_cents"] or 0) for s in self._stages(db, job_id)
                            if s["name"] != "chirp_independent")
            over_budget = (other + actual_cents > stage["estimate_cents"]
                    or (row["estimated_cents"] and actual_cents > row["estimated_cents"])
                    or committed + other + actual_cents > job["approved_cents"])
            db.execute("UPDATE v2_chirp_operations SET status='DONE',result_sha=?,"
                       "actual_cents=?,updated_at=? WHERE job_id=? AND window_id=?",
                       (evidence_sha, actual_cents, _now(), job_id, window_id))
            if over_budget or frozen:
                self._record_budget_overrun(
                    db, job_id=job_id, stage="chirp_independent",
                    actual_cents=other + actual_cents, evidence_sha=evidence_sha,
                )

    def finish_recovered_chirp(self, job_id: str, *, evidence_sha: str,
                               verifier: Any) -> None:
        """Close a restarted stage only after independently checking the merge."""
        if not isinstance(evidence_sha, str) or len(evidence_sha) != 64 or any(
                c not in "0123456789abcdef" for c in evidence_sha):
            raise V2Conflict("invalid merged Chirp SHA")
        with self._tx() as db:
            job = self._job(db, job_id)
            stage = db.execute("SELECT * FROM v2_stages WHERE job_id=? AND name='chirp_independent'",
                               (job_id,)).fetchone()
            windows = db.execute(
                "SELECT * FROM v2_chirp_operations WHERE job_id=? "
                "ORDER BY CASE purpose WHEN 'BASE' THEN 0 ELSE 1 END,start_ms,window_id",
                (job_id,),
            ).fetchall()
            if (job["status"] != "PROCESSING" or stage is None
                    or stage["state"] != "RUNNING" or not windows
                    or any(row["status"] != "DONE" for row in windows)):
                raise V2Conflict("cannot finish incomplete Chirp stage")
            if not callable(verifier) or not verifier(
                    job_id=job_id, source_sha=job["source_sha"],
                    evidence_sha=evidence_sha, windows=[dict(row) for row in windows]):
                raise V2Conflict("merged Chirp evidence not verified")
            actual = sum(int(row["actual_cents"]) for row in windows)
            if actual > stage["estimate_cents"]:
                raise V2Conflict("Chirp merge usage exceeds stage reservation")
            db.execute("UPDATE v2_stages SET state='COMPLETE',actual_cents=?,"
                       "evidence_sha=?,claim_hash=NULL,updated_at=? WHERE job_id=? "
                       "AND name='chirp_independent'", (actual, evidence_sha, _now(), job_id))

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
