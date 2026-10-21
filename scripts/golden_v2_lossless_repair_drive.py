"""Quarantined historical repair helper; the CLI deliberately refuses dispatch.

The gap-only VAD plan is not a reviewed lexical repair plan. Production calls
must instead use the sanctioned source-bound explicit-target repair service.
The helper remains covered for claim handling; no owner/listening identity or
repair reservation is assumed, and this script is not a production worker.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["COURSE_TRANSCRIPT_V2_ARTIFACT_ROOT"])
DB = Path(os.environ["COURSE_TRANSCRIPT_V2_LEDGER_PATH"])
sys.path.insert(0, "/app")

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_chirp_stage import finish_approved_chirp, submit_planned_chirp
from app.pipeline.golden_v2_chirp_recovery_worker import create_production_chirp_gateway
from app.pipeline.golden_v2_chirp_repair import (
    propose_standard_repair_plan, approve_and_stage_standard_repairs,
    verified_prior_chirp_evidence,
)
from app.pipeline.golden_v2_pricing import standard_chirp_cents
from app.providers.golden_v2_acoustic_qa import inspect_v2_acoustic
from app.providers.golden_v2_audio_staging import file_sha256

BUCKET = os.environ["GCS_BUCKET"]
PREFIX = "golden-v2-runtime"
AGG_CAP = 1000
OWNER = os.environ.get("COURSE_TRANSCRIPT_V2_REPAIR_OWNER_ACTOR", "")
REVIEWER = os.environ.get("COURSE_TRANSCRIPT_V2_REPAIR_REVIEWER", "")
REVIEW_REFERENCE = os.environ.get("COURSE_TRANSCRIPT_V2_REPAIR_REVIEW_REFERENCE", "")
WORKER = "golden-v2-lossless-repair"

JOBS = {
    "v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be": "251025",
    "v2p_560465ab6db05e3da593837f4d76c630": "251018",
    "v2p_62a124bdd4595aca91c53085c3732ca7": "261017",
}


def log(event, **kw):
    print(json.dumps({"event": event, **kw}, ensure_ascii=False), flush=True)


def deliver(ledger, jid, label):
    jd = ROOT / "jobs" / jid
    g = jd / "golden-v2"
    state = ledger.status(jid)
    stages = {s["name"]: s for s in state["stages"]}
    source_sha = state["source_sha"]
    log("START", job_id=jid, label=label, status=state["status"],
        revision=state["revision"],
        stages={s["name"]: s["state"] for s in state["stages"]})
    if state["status"] != "NEEDS_REVIEW":
        log("SKIP", job_id=jid, status=state["status"])
        return

    prior_chirp = stages["chirp_independent"]["evidence_sha"]
    prior_qa = stages["golden_qa"]["evidence_sha"]

    # The ledger already holds its transaction when it invokes this verifier.
    # Use the supplied snapshot and filesystem evidence; never re-enter ledger.
    verify = verified_prior_chirp_evidence(jd)

    # Reserve the repair spend from the unused Gemini reservation.
    transfer = int(os.environ.get("COURSE_TRANSCRIPT_V2_REPAIR_RESERVATION_CENTS", "0"))
    resumed = ledger.resume_chirp_review_for_repair(
        jid, revision=state["revision"], source_sha=source_sha,
        previous_chirp_evidence_sha=prior_chirp, previous_qa_evidence_sha=prior_qa,
        transfer_cents=transfer, reviewer=REVIEWER, review_reference=REVIEW_REFERENCE,
        worker_id=WORKER, aggregate_cap_cents=AGG_CAP, evidence_verifier=verify)
    log("CHIRP_REOPENED", job_id=jid, transfer_cents=transfer,
        estimate_cents=resumed["estimate_cents"])

    # resume_chirp_review_for_repair atomically creates and returns the claim.
    # A second claim_next would reject the already-RUNNING stage.
    claim = resumed
    ledger.assert_stage_claim(claim, stage="chirp_independent")
    log("CLAIMED", job_id=jid, stage=claim["stage"])

    # Recompute the lossless merge from the immutable provider raw and run the
    # real acoustic assessment over the recomputed timebase.
    merged = finish_approved_chirp(ledger=ledger, job_id=jid, job_dir=jd,
                                   acoustic_inspector=inspect_v2_acoustic)
    log("MERGE_REBUILT", job_id=jid, words=len(merged["words"]),
        chars=sum(len(w["word"].strip()) for w in merged["words"]),
        acoustic_gate=merged["acoustic_gate"],
        repair_intervals=len(merged.get("repair_plan", {}).get("intervals", [])))
    if merged["acoustic_gate"] == "PASS":
        log("ACOUSTIC_PASS_NO_PAID_REPAIR", job_id=jid)
        return

    proposal = propose_standard_repair_plan(
        ledger=ledger, claim=claim, job_dir=jd,
        normalized_audio=jd / "normalized.flac",
        acoustic_report_path=Path(merged["acoustic_qa_path"]),
        bucket_name=BUCKET, object_prefix=PREFIX,
        estimate_window_cents=lambda item: standard_chirp_cents(
            (item["end_ms"] - item["start_ms"]) / 1000))
    log("REPAIR_PROPOSED", job_id=jid, windows=len(proposal["window_rows"]),
        estimated_cents=proposal["estimated_cents"],
        plan_sha=proposal["proposal_sha256"],
        spans=[(r["start_ms"], r["end_ms"]) for r in proposal["window_rows"]])

    from google.cloud import storage
    approved = approve_and_stage_standard_repairs(
        ledger=ledger, claim=claim, proposal=proposal,
        approved_cents=proposal["estimated_cents"],
        approval_reference=REVIEW_REFERENCE, approving_actor=OWNER,
        bucket_name=BUCKET, object_prefix=PREFIX,
        storage_client=storage.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"]))
    log("REPAIR_STAGED", job_id=jid, plan_sha=approved["plan_sha"],
        uploaded=approved["uploaded_windows"])

    gateway = create_production_chirp_gateway(
        project=os.environ["GOOGLE_CLOUD_PROJECT"], dynamic_batching=False)
    rows = submit_planned_chirp(ledger=ledger, claim=claim, gateway=gateway)
    log("REPAIR_SUBMITTED", job_id=jid,
        submitted=[r["window_id"] for r in rows if r["status"] == "SUBMITTED"])


def main():
    # VAD detects activity, not lexical omissions. The historical gap-only
    # driver would pay for 261017 noise and miss 251018's shifted 85 tokens.
    # Keep this helper non-executable until a source-bound reviewed target plan
    # is supplied through the sanctioned repair service, not caller constants.
    log("EXECUTION_DISABLED", reason="unreviewed VAD-only repair targets",
        provider_submissions=0, ledger_mutations=0, drive_mutations=0)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
