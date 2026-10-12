"""Read-only Golden V2 paid-stage dispatch preview and review fingerprint.

This is NOT a provider executor or an owner approval endpoint. It deliberately
does not mint a claim, create paid request slots, or authenticate live cloud
credentials. The eventual trusted runner must recheck live Drive/provider/
pricing, acquire a revision/source/stage-bound SQLite claim with the same
aggregate cap, then use the existing audit-first provider adapter.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import MODEL_BY_STAGE, approved_trial_exposure
from app.pipeline.golden_v2_paid_readiness import inspect_paid_readiness


def _digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def preview_paid_dispatch(
    *, database: Path, artifact_root: Path, job_id: str,
    aggregate_trial_cap_cents: int = 1000,
    expected_source_sha: str | None = None,
) -> dict[str, Any]:
    """Preview the *whole* trial exposure; never interpret success as consent.

    Aggregate exposure counts already charged stages and conservatively
    reserves unfinished approved stages for every job in this isolated ledger.
    All monetary units are **USD cents**, not a provider quote.
    """
    if (type(aggregate_trial_cap_cents) is not int
            or not 1 <= aggregate_trial_cap_cents <= 100000):
        raise ValueError("aggregate trial cap must be positive USD cents")
    readiness = inspect_paid_readiness(
        database=database, artifact_root=artifact_root, job_id=job_id,
        max_approved_cents=aggregate_trial_cap_cents,
        expected_source_sha=expected_source_sha,
    )
    blockers = set(readiness["blockers"])
    version: int | None = None
    stage_estimate: int | None = None
    exposure: int | None = None
    review_digest: str | None = None
    stage = readiness["next_stage"]
    source_sha = readiness["source_sha256"]
    model = MODEL_BY_STAGE.get(stage or "")
    if (not Path(database).is_symlink() and not Path(artifact_root).is_symlink()
            and Path(database).is_file()
            and Path(database).resolve().parent == Path(artifact_root).resolve()):
        try:
            with closing(sqlite3.connect(
                f"{Path(database).resolve().as_uri()}?mode=ro", uri=True,
            )) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                exposure = approved_trial_exposure(db)
                if exposure > aggregate_trial_cap_cents:
                    blockers.add("AGGREGATE_TRIAL_CAP_EXCEEDED")
                # An unresolved billed request cannot be bounded with a
                # locally guessed upper limit, irrespective of estimates.
                unknown = db.execute(
                    "SELECT 1 FROM v2_stages s JOIN v2_jobs j ON j.id=s.job_id "
                    "WHERE j.approved_cents IS NOT NULL AND "
                    "s.name IN ('gemini_primary','chirp_independent','third_optional') "
                    "AND s.state IN ('RUNNING','OUTCOME_UNKNOWN','OVER_BUDGET') LIMIT 1"
                ).fetchone()
                if unknown:
                    blockers.add("UNRECONCILED_GLOBAL_PAID_STAGE")
                job = db.execute(
                    "SELECT revision,source_sha,approval_reference,approved_cents,"
                    "mode FROM v2_jobs WHERE id=?", (job_id,),
                ).fetchone()
                if job is not None and stage in MODEL_BY_STAGE:
                    version = job["revision"]
                    row = db.execute(
                        "SELECT estimate_cents,state FROM v2_stages "
                        "WHERE job_id=? AND name=?", (job_id, stage),
                    ).fetchone()
                    if row:
                        stage_estimate = row["estimate_cents"]
                    if (job["source_sha"] != source_sha
                            or job["approved_cents"] != readiness["approved_cents"]
                            or row is None or row["state"] != "PENDING"):
                        blockers.add("PREVIEW_LEDGER_CHANGED_SINCE_PREFLIGHT")
                    if stage == "third_optional":
                        cmp_row = db.execute(
                            "SELECT state,review_required FROM v2_stages "
                            "WHERE job_id=? AND name='compare'", (job_id,),
                        ).fetchone()
                        if job["mode"] == "FLASH_CHIRP" or (
                                job["mode"] == "SMART_2PLUS"
                                and cmp_row is not None
                                and cmp_row["state"] == "COMPLETE"
                                and cmp_row["review_required"] == 0):
                            blockers.add("OPTIONAL_THIRD_MODEL_WILL_BE_SKIPPED")
                    if not blockers:
                        # Identifies the exact reviewed state. Not a signature,
                        # bearer token, budget approval or proof of IAM.
                        review_digest = _digest({
                            "job_id": job_id,
                            "revision": version,
                            "source_sha256": source_sha,
                            "stage": stage,
                            "stage_estimate_cents": stage_estimate,
                            "approved_cents": job["approved_cents"],
                            "approval_reference_sha256": hashlib.sha256(
                                str(job["approval_reference"]).encode()).hexdigest(),
                            "aggregate_exposure_cents": exposure,
                            "aggregate_cap_cents": aggregate_trial_cap_cents,
                        })
        except sqlite3.Error:
            blockers.add("TRIAL_EXPOSURE_LEDGER_UNREADABLE")

    return {
        "schema": "golden-v2-paid-dispatch-preview-v1",
        "job_id": job_id,
        "source_sha256": source_sha,
        "stage": stage,
        "proposed_provider_model": model,
        "job_revision": version,
        "stage_estimate_usd_cents": stage_estimate,
        "approved_job_usd_cents": readiness["approved_cents"],
        "aggregate_trial_cap_usd_cents": aggregate_trial_cap_cents,
        "aggregate_exposure_usd_cents": exposure,
        "review_fingerprint_sha256": review_digest,
        "blockers": sorted(blockers),
        "local_review_ready": not blockers,
        "live_drive_revision_verified": False,
        "provider_capability_verified": False,
        "provider_pricing_verified": False,
        "owner_paid_call_authorization_verified": False,
        "provider_calls_allowed": False,
        "ledger_modified": False,
        "drive_mutation_started": False,
        "read_only": True,
        "next_action": "LIVE_CHECKS_AND_EXPLICIT_SOURCE_APPROVAL_REQUIRED"
                       if not blockers else "BLOCKED_REQUIRE_RECONCILIATION",
    }