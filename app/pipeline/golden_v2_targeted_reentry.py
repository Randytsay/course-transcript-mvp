"""Source-scoped, cap-reserved Chirp paid-stage reentry for already-reconciled work.

This module is deliberately not a provider dispatcher. It must never POST,
upload to GCS, auto-sign an owner permission, or mark an acoustic QA as PASS.
It CAS-rotates a dead/exhausted old stage claim *only after* a trusted approval
and independently verified immutable ledger/provider evidence. Reserve the
four-job incremental cap in the same SQLite transaction as the stage claim.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict, approved_trial_exposure, _now

ONE_DOLLAR_CENTS = 100
SOURCE_BOUND_JOB_COUNT = 4


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _parse_old_timestamp(value: str) -> float:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError("missing timezone")
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError) as exc:
        raise V2Conflict("unverifiable paid-stage heartbeat") from exc


def reserve_targeted_repair_and_reenter(
    *, ledger: GoldenV2Ledger,
    job_id: str, expected_revision: int, expected_source_sha: str,
    assessment_sha256: str, source_scopes: dict[str, str],
    batch_reference: str, batch_authorized_cents: int,
    requested_cents: int, worker_id: str, actor: str,
    trusted_owner_grant_verifier: Callable[..., bool],
    evidence_verifier: Callable[..., bool],
    aggregate_cap_cents: int = 1000,
    minimum_stale_seconds: int = 300,
) -> dict[str, Any]:
    """Reserve at most 100 *new* cents across a fixed four-source owner scope.

    Caller must verify original human-owner grant (e.g. via authenticated API
    and signed authorization) and exact raw/audio/acoustic SHA outside this
    transaction. Existing operation results and bills are preserved. The
    returned token is ephemeral and must never be logged.
    """
    sha = lambda s: isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)
    if (type(expected_revision) is not int or expected_revision < 0
            or not all(sha(x) for x in (expected_source_sha, assessment_sha256))
            or type(batch_authorized_cents) is not int
            or batch_authorized_cents != ONE_DOLLAR_CENTS
            or type(requested_cents) is not int or requested_cents < 1
            or requested_cents > batch_authorized_cents
            or type(aggregate_cap_cents) is not int or not 1 <= aggregate_cap_cents <= 100000
            or type(minimum_stale_seconds) is not int or minimum_stale_seconds < 300
            or len(source_scopes) != SOURCE_BOUND_JOB_COUNT
            or any(not k.startswith("v2p_") or not sha(v) for k,v in source_scopes.items())
            or source_scopes.get(job_id) != expected_source_sha
            or not batch_reference or len(batch_reference) > 256
            or not worker_id.strip() or not actor.strip()
            or not callable(trusted_owner_grant_verifier)
            or not callable(evidence_verifier)):
        raise V2Conflict("invalid exact-source capped reentry request")
    scope_sha = _hash({"ref": batch_reference, "sources": source_scopes,
                       "max_cents": batch_authorized_cents, "stage": "chirp_3_standard_targeted_repair"})
    # Explicit evidence verification can be slow; re-check every fact and the
    # same source SHA inside the reserved CAS transaction before commit.
    if trusted_owner_grant_verifier(
        reference=batch_reference, actor=actor, source_scopes=source_scopes,
        authorized_cents=batch_authorized_cents, scope_sha=scope_sha) is not True:
        raise V2Conflict("authenticated owner batch grant not verified")
    with ledger._tx() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS v2_targeted_repair_batch_budget (
                scope_sha TEXT PRIMARY KEY, reference TEXT NOT NULL UNIQUE,
                authorized_cents INTEGER NOT NULL, source_scopes_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
        db.execute("""
            CREATE TABLE IF NOT EXISTS v2_targeted_repair_claims (
                job_id TEXT NOT NULL PRIMARY KEY, scope_sha TEXT NOT NULL,
                source_sha TEXT NOT NULL, assessment_sha TEXT NOT NULL,
                reserved_cents INTEGER NOT NULL, previous_revision INTEGER NOT NULL,
                previous_chirp_state TEXT NOT NULL, previous_chirp_evidence_sha TEXT,
                previous_claim_hash TEXT, prior_stage_snapshot_sha TEXT NOT NULL,
                actor TEXT NOT NULL, worker_id TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(scope_sha) REFERENCES v2_targeted_repair_batch_budget(scope_sha)
            )""")
        existing_budget = db.execute(
            "SELECT * FROM v2_targeted_repair_batch_budget WHERE reference=?",
            (batch_reference,)).fetchone()
        scopes_json = json.dumps(source_scopes, sort_keys=True, separators=(",", ":"))
        if existing_budget and (existing_budget["scope_sha"] != scope_sha
                                or existing_budget["authorized_cents"] != batch_authorized_cents
                                or existing_budget["source_scopes_json"] != scopes_json):
            raise V2Conflict("batch owner approval was changed or replayed")
        prior = db.execute(
            "SELECT * FROM v2_targeted_repair_claims WHERE job_id=?", (job_id,)
        ).fetchone()
        if prior is not None:
            raise V2Conflict("job already received a targeted paid reentry claim")
        reserved = db.execute(
            "SELECT COALESCE(SUM(reserved_cents),0) FROM v2_targeted_repair_claims WHERE scope_sha=?",
            (scope_sha,)).fetchone()[0]
        if reserved + requested_cents > batch_authorized_cents:
            raise V2Conflict("incremental four-source owner cap exceeded")
        job = db.execute("SELECT * FROM v2_jobs WHERE id=?", (job_id,)).fetchone()
        stages = [dict(s) for s in db.execute(
            "SELECT * FROM v2_stages WHERE job_id=? ORDER BY ord", (job_id,))]
        if job is None:
            raise V2Conflict("job missing")
        byname = {s["name"]:s for s in stages}
        chirp = byname["chirp_independent"]
        gemini = byname["gemini_primary"]
        if (job["revision"] != expected_revision
                or job["source_sha"] != expected_source_sha
                or job["mode"] != "FLASH_CHIRP"
                or job["approved_cents"] is None
                or gemini["state"] not in ("COMPLETE","REVIEW_REQUIRED")
                or chirp["state"] not in ("RUNNING","REVIEW_REQUIRED")
                or job["status"] not in ("PROCESSING","NEEDS_REVIEW")
                or ((job["status"]=="PROCESSING") != (chirp["state"]=="RUNNING"))):
            raise V2Conflict("target job state/source/CAS drift")
        if (chirp["state"] == "RUNNING"
                and _parse_old_timestamp(chirp["updated_at"]) < minimum_stale_seconds):
            raise V2Conflict("old paid worker is still inside active heartbeat grace")
        operations = [dict(row) for row in db.execute(
            "SELECT * FROM v2_chirp_operations WHERE job_id=? ORDER BY window_id", (job_id,))]
        if (not operations or any(
                row["status"] != "DONE" or not row["operation_name"]
                or not sha(row["result_sha"]) or row["actual_cents"] is None
                or row["purpose"] != "BASE" for row in operations)):
            raise V2Conflict("old Chirp provider operations not definitively reconciled")
        chirp_spent = sum(int(row["actual_cents"]) for row in operations)
        if (chirp["actual_cents"] is not None
                and int(chirp["actual_cents"]) != chirp_spent):
            raise V2Conflict("Chirp ledger stage cost diverged from receipts")
        other_spent = sum(int(s["actual_cents"] or 0)
                          for s in stages if s["name"]!="chirp_independent")
        if (other_spent + chirp_spent + requested_cents > int(job["approved_cents"])
                or int(gemini["estimate_cents"]) - int(gemini["actual_cents"] or 0)
                < requested_cents):
            raise V2Conflict("existing per-source approval or transferable reserve insufficient")
        if (sum(int(s["estimate_cents"]) for s in stages) > int(job["approved_cents"])
                or approved_trial_exposure(db) > aggregate_cap_cents):
            raise V2Conflict("global approved exposure is not covered")
        unknown = db.execute(
            "SELECT 1 FROM v2_chirp_operations WHERE status IN "
            "('PLANNED','SUBMITTING','SUBMITTED','REVIEW_REQUIRED') LIMIT 1").fetchone()
        if unknown:
            raise V2Conflict("global unfinished/unknown paid provider window")
        # Another active paid stage is never ignored unless its exact known
        # window set is reconciled, preventing takeover while billed work runs.
        for active in db.execute(
            "SELECT s.job_id,s.name,s.state FROM v2_stages s "
            "JOIN v2_jobs j ON j.id=s.job_id "
            "WHERE j.approved_cents IS NOT NULL AND s.name IN "
            "('gemini_primary','chirp_independent','third_optional') "
            "AND s.state IN ('RUNNING','OUTCOME_UNKNOWN','OVER_BUDGET')"
        ):
            if active["job_id"] != job_id or active["name"] != "chirp_independent":
                raise V2Conflict("another unknown or running paid stage blocks reentry")
        if evidence_verifier(
                job_id=job_id, source_sha=expected_source_sha,
                assessment_sha=assessment_sha256,
                prior_chirp_sha=chirp["evidence_sha"],
                operations=operations, stage=chirp) is not True:
            raise V2Conflict("source and immutable raw/acoustic evidence not verified")
        if not existing_budget:
            db.execute(
                "INSERT INTO v2_targeted_repair_batch_budget VALUES(?,?,?,?,?)",
                (scope_sha,batch_reference,batch_authorized_cents,scopes_json,_now()))
        snapshot_sha = _hash(stages)
        db.execute(
            "INSERT INTO v2_targeted_repair_claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id,scope_sha,expected_source_sha,assessment_sha256,
             requested_cents,expected_revision,chirp["state"],chirp["evidence_sha"],
             chirp["claim_hash"],snapshot_sha,actor,worker_id,_now()))
        token = secrets.token_urlsafe(32)
        db.execute(
            "UPDATE v2_stages SET estimate_cents=estimate_cents-?,updated_at=? "
            "WHERE job_id=? AND name='gemini_primary'",
            (requested_cents,_now(),job_id))
        db.execute(
            "UPDATE v2_stages SET estimate_cents=estimate_cents+?,state='RUNNING',"
            "actual_cents=NULL,evidence_sha=NULL,claim_hash=?,updated_at=? "
            "WHERE job_id=? AND name='chirp_independent'",
            (requested_cents,hashlib.sha256(token.encode()).hexdigest(),_now(),job_id))
        db.execute(
            "UPDATE v2_jobs SET status='PROCESSING',revision=revision+1 WHERE id=?",
            (job_id,))
        # No old op rows were mutated or removed. This token is newly minted
        # by the transactional ledger, never reconstructed from a stored hash.
        return {"job_id":job_id, "stage":"chirp_independent","claim":token,
                "source_sha":expected_source_sha,
                "estimate_cents":int(chirp["estimate_cents"])+requested_cents,
                "new_reserved_cents":requested_cents, "scope_sha":scope_sha,
                "next_revision":expected_revision+1}



def verify_persisted_owner_targeted_grant(
    *, grant_path: Path, reference: str, actor: str,
    source_scopes: dict[str, str], authorized_cents: int, scope_sha: str,
) -> bool:
    """Verify an independent operator-signed, immutable one-dollar grant.

    An owner-authenticated writer must produce this signed grant separately;
    this verifier deliberately never creates or signs grants.
    """
    from app.pipeline.golden_v2_runtime_services import signed_grant
    grant_path = Path(grant_path)
    if (grant_path.is_symlink() or not grant_path.is_file()
            or grant_path.stat().st_size > 8192):
        raise V2Conflict("persisted owner budget grant missing or unsafe")
    try:
        envelope = json.loads(grant_path.read_text(encoding="utf-8"))
        payload = envelope["grant"]
        signature = envelope["signature"]
        expected = signed_grant(payload)["signature"]
        if (not isinstance(signature,str)
                or not hmac.compare_digest(signature,expected)
                or set(payload) != {"schema","reference","actor","source_scopes",
                                    "authorized_cents","scope_sha","model",
                                    "allow_base_replay","allow_production_deploy",
                                    "expires_at"}):
            raise V2Conflict("owner targeted repair signature/schema mismatch")
        if (payload["schema"] != "golden-v2-four-source-repair-budget-v1"
                or payload["reference"] != reference
                or payload["actor"] != actor
                or payload["source_scopes"] != source_scopes
                or payload["authorized_cents"] != authorized_cents
                or payload["scope_sha"] != scope_sha
                or payload["model"] != "chirp_3_standard_targeted_repair"
                or payload["allow_base_replay"] is not False
                or payload["allow_production_deploy"] is not False
                or not isinstance(payload["expires_at"],str)
                or datetime.fromisoformat(payload["expires_at"]).astimezone(timezone.utc)
                      <= datetime.now(timezone.utc)):
            raise V2Conflict("owner targeted repair grant was widened or expired")
        return True
    except (KeyError,TypeError,ValueError) as exc:
        raise V2Conflict("malformed signed owner targeted repair grant") from exc


def verify_source_bound_repair_bundle(
    *, root: Path, job_id: str, source_sha: str,
    assessment_sha: str, prior_chirp_sha: str | None,
    operations: list[dict[str, Any]], stage: dict[str, Any],
) -> bool:
    """Read original source/operation SHA and acoustic-positive clip ancestry.

    Never infer a recognized word from VAD. Only verify that every intended
    *new* paid clip is an immutable slice of the exact approved FLAC source.
    """
    from app.pipeline.golden_v2_pricing import standard_chirp_cents, assert_current
    from app.providers.golden_v2_audio_staging import file_sha256

    root = Path(root)
    if root.is_symlink() or not job_id.startswith("v2p_"):
        raise V2Conflict("invalid targeted repair root")
    job = root / "jobs" / job_id
    folder = job / "golden-v2"
    if job.is_symlink() or folder.is_symlink() or not folder.is_dir():
        raise V2Conflict("unsafe targeted repair source")
    if not (len(source_sha) == 64 and file_sha256(job/"normalized.flac") == source_sha):
        raise V2Conflict("normalized source FLAC SHA mismatch")
    preflight = json.loads((job/"source-preflight.json").read_text())
    if preflight.get("source_sha256") != source_sha:
        raise V2Conflict("Drive source preflight SHA mismatch")
    for operation in operations:
        evidence = folder/"chirp-evidence"/operation["window_id"]
        raw = evidence/"provider-raw.json"
        metadata = evidence/"window.json"
        if (raw.is_symlink() or metadata.is_symlink() or not raw.is_file()
                or not metadata.is_file()
                or file_sha256(raw) != operation["result_sha"]):
            raise V2Conflict("previous Chirp raw receipt is not immutable")
        doc = json.loads(metadata.read_text())
        if (doc.get("raw_sha") != operation["result_sha"]
                or doc.get("source_sha") != source_sha
                or doc.get("request_id") != operation["request_id"]
                or doc.get("operation_name") != operation["operation_name"]
                or doc.get("actual_cents") != operation["actual_cents"]):
            raise V2Conflict("previous provider receipt metadata mismatch")
    if prior_chirp_sha is not None:
        prior = folder / f"chirp-merged-candidate-{prior_chirp_sha[:20]}.json"
        if (prior.is_symlink() or not prior.is_file()
                or file_sha256(prior) != prior_chirp_sha):
            raise V2Conflict("ledger-attested historical merge evidence missing")
        archived = json.loads(prior.read_text(encoding="utf-8"))
        if (archived.get("source_sha") != source_sha
                or archived.get("timing_authority") != "chirp_3"):
            raise V2Conflict("historical source/timing lineage changed")
        # The canonical chirp-merged.json may be an already-prepared newer
        # review candidate. Preserve it without falsely treating it as the
        # historical ledger-attested COMPLETE/REVIEW_REQUIRED stage receipt.
    reports = [
        f for glob in ("chirp-acoustic-qa-*.json", "acoustic-fullscan-20261024-*.json")
        for f in folder.glob(glob) if not f.is_symlink()
        and f.is_file() and file_sha256(f) == assessment_sha
    ]
    if len(reports) != 1:
        raise V2Conflict("acoustic assessment identity ambiguous or absent")
    qa = json.loads(reports[0].read_text())
    if (qa.get("schema") != "golden-v2-acoustic-qa-v1"
            or qa.get("status") != "REVIEW"
            or qa.get("source_sha256") != source_sha
            or qa.get("unexamined_gap_count") != 0
            or qa.get("repair_plan", {}).get("strategy") != "STANDARD_BATCH"
            or qa["repair_plan"].get("submit_authorized") is not False):
        raise V2Conflict("positive source-bound QA required; no unknown gaps")
    proposals = qa["repair_plan"]["intervals"]
    gaps = qa["gaps_examined"]
    if not proposals:
        raise V2Conflict("no speech-positive target to repair")
    for proposal in proposals:
        targets = proposal.get("target_intervals")
        if not isinstance(targets,list) or not targets:
            raise V2Conflict("target has no positive acoustic evidence")
        for target in targets:
            n = target.get("gap_index")
            if (type(n) is not int or not 0 <= n < len(gaps)
                    or gaps[n].get("disposition") != "SPEECH_CONFIRMED"
                    or not gaps[n]["start_ms"] <= target["start_ms"]
                    < target["end_ms"] <= gaps[n]["end_ms"]):
                raise V2Conflict("target includes an unconfirmed acoustic gap")
    bundles = list(folder.glob("review-only-target-clips-*.json"))
    if len(bundles) != 1 or bundles[0].is_symlink():
        raise V2Conflict("one immutable local clip manifest required")
    bundle = json.loads(bundles[0].read_text())
    if (bundle.get("schema") != "golden-v2-unapproved-review-clips-20261024"
            or bundle.get("source_sha256") != source_sha
            or bundle.get("acoustic_qa_sha256") != assessment_sha
            or bundle.get("provider_calls_started") is not False
            or bundle.get("gcs_uploaded") is not False
            or bundle.get("submit_authorized") is not False):
        raise V2Conflict("local clip bundle not an exact non-paid proposal")
    clips = bundle.get("clips")
    if (not isinstance(clips,list) or not 1 <= len(clips) <= 24
            or bundle.get("total_clips") != len(clips)):
        raise V2Conflict("invalid number of source-bound review clips")
    cents = 0
    duration = 0
    bounds = []
    for row in clips:
        name = row.get("filename")
        if (not isinstance(name,str) or "/" in name or "\\" in name
                or ".." in name or not name.endswith(".flac")):
            raise V2Conflict("unsafe local audio clip name")
        audio = folder/"review-only-clips-20261024"/name
        start,end = row.get("start_ms"),row.get("end_ms")
        if (audio.is_symlink() or type(start) is not int or type(end) is not int
                or start < 0 or end <= start or end-start > 300000
                or not audio.is_file() or audio.stat().st_size != row.get("bytes")
                or file_sha256(audio) != row.get("sha256")):
            raise V2Conflict("local FLAC slice SHA/range mismatch")
        if not any(start <= target["start_ms"] < target["end_ms"] <= end
                   for p in proposals for target in p["target_intervals"]):
            raise V2Conflict("local clip is not bound to speech-positive QA target")
        bounds.append((start,end))
        duration += end-start
        cents += standard_chirp_cents((end-start)/1000)
    sorted_bounds=sorted(bounds)
    # A Standard repair of a >5-minute speech-positive gap may retain up to
    # 5 seconds of acoustic context in two adjacent clips. The positive
    # target_intervals themselves must still be nonoverlapping; otherwise
    # this is duplicate billable speech, not legitimate context.
    ordered_targets=sorted((target["start_ms"],target["end_ms"])
                           for p in proposals for target in p["target_intervals"])
    if (len(set(bounds)) != len(bounds)
            or any(x[1] - y[0] > 5000 for x,y in zip(sorted_bounds,sorted_bounds[1:]))
            or any(x[1] > y[0] for x,y in zip(ordered_targets,ordered_targets[1:]))
            or duration != bundle["audio_duration_ms"]
            or cents != bundle["conservative_standard_estimate_cents"]
            or cents != sum(bundle["per_clip_estimate_cents"])):
        raise V2Conflict("clip overlap or conservative price drift")
    assert_current()
    return True


def recover_interrupted_targeted_claim(
    *, ledger: GoldenV2Ledger, job_id: str, scope_sha: str,
    expected_revision: int, minimum_stale_seconds: int=300,
) -> dict[str,Any]:
    """Reissue a lost *local* claim only when every old/new provider result is known.

    Never resubmit a SUBMITTING or SUBMITTED operation. Existing owner budget
    reservation is reused, so a crash cannot create another financial grant.
    A PLANNED operation is safe to resume only with exact staged identity.
    """
    sha = lambda x: isinstance(x,str) and len(x)==64 and all(c in "0123456789abcdef" for c in x)
    if (not sha(scope_sha) or type(expected_revision) is not int
            or type(minimum_stale_seconds) is not int or minimum_stale_seconds<300):
        raise V2Conflict("invalid interrupted targeted claim recovery")
    with ledger._tx() as db:
        grant=db.execute("SELECT * FROM v2_targeted_repair_claims WHERE job_id=?",(job_id,)).fetchone()
        job=db.execute("SELECT * FROM v2_jobs WHERE id=?",(job_id,)).fetchone()
        chirp=db.execute("SELECT * FROM v2_stages WHERE job_id=? AND name='chirp_independent'",(job_id,)).fetchone()
        if (grant is None or job is None or chirp is None
                or grant["scope_sha"]!=scope_sha
                or job["revision"]!=expected_revision
                or job["source_sha"]!=grant["source_sha"]
                or job["status"]!="PROCESSING" or chirp["state"]!="RUNNING"
                or _parse_old_timestamp(chirp["updated_at"])<minimum_stale_seconds):
            raise V2Conflict("targeted claim cannot be safely reclaimed")
        statuses=[r["status"] for r in db.execute(
            "SELECT status FROM v2_chirp_operations WHERE job_id=?",(job_id,))]
        if not statuses or any(s not in ("DONE","PLANNED") for s in statuses):
            raise V2Conflict("unknown/new paid operation blocks claim recovery")
        if db.execute("SELECT 1 FROM v2_chirp_operations WHERE job_id!=? "
                      "AND status IN ('PLANNED','SUBMITTING','SUBMITTED','REVIEW_REQUIRED') LIMIT 1",(job_id,)).fetchone():
            raise V2Conflict("other unresolved provider work blocks claim recovery")
        db.execute("""
            CREATE TABLE IF NOT EXISTS v2_targeted_repair_claim_resumptions (
                job_id TEXT NOT NULL, from_revision INTEGER NOT NULL,
                old_claim_hash TEXT NOT NULL, new_claim_hash TEXT NOT NULL,
                resumed_at TEXT NOT NULL, PRIMARY KEY(job_id,from_revision)
            )""")
        token=secrets.token_urlsafe(32)
        newhash=hashlib.sha256(token.encode()).hexdigest()
        db.execute("INSERT INTO v2_targeted_repair_claim_resumptions VALUES(?,?,?,?,?)",
                   (job_id,expected_revision,chirp["claim_hash"],newhash,_now()))
        db.execute("UPDATE v2_stages SET claim_hash=?,updated_at=? "
                   "WHERE job_id=? AND name='chirp_independent'",(newhash,_now(),job_id))
        db.execute("UPDATE v2_jobs SET revision=revision+1 WHERE id=?",(job_id,))
        return {"job_id":job_id,"stage":"chirp_independent","claim":token,
                "source_sha":job["source_sha"],"estimate_cents":chirp["estimate_cents"],
                "reserved_reused_cents":grant["reserved_cents"],"next_revision":expected_revision+1}
