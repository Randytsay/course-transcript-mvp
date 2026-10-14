"""Trusted controller entrypoint for ONE approved general V2 paid stage.

No credential/client construction, HTTP route, daemon or automatic approval.
The controller must supply independently backed owner/capability/pricing
checks and real clients. The four-minute smoke authorization never authorizes
a full-source job. Tests inject fake clients; imports make no cloud calls.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Callable

from app.jobs.drive_api import read_drive_media_metadata
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_paid_dispatch import preview_paid_dispatch
from app.pipeline.golden_v2_drive_revision import verify_live_drive_revision
from app.pipeline.golden_v2_primary_stage import execute_approved_primary_stage
from app.pipeline.golden_v2_third_stage import execute_approved_third_stage
from app.pipeline.golden_v2_chirp_stage import plan_approved_chirp, submit_planned_chirp


def execute_trusted_paid_stage(
    *, database: Path, artifact_root: Path, job_id: str,
    expected_revision: int, expected_source_sha: str, expected_stage: str,
    aggregate_cap_cents: int,
    verify_owner: Callable[[dict[str, Any]], bool],
    verify_capability_and_pricing: Callable[[dict[str, Any]], bool],
    stage_options: dict[str, Any],
    read_metadata: Callable[[str], dict[str, Any]] = read_drive_media_metadata,
    worker_id: str = "golden-v2-trusted-controller",
) -> dict[str, Any]:
    """Revalidate source/approval → atomic claim → audit-first stage adapter.

    Never loops to the next stage or retries. Chirp returns submitted durable
    operations, to be polled by the existing recovery worker (no re-submit).
    Before-claim failures leave SQLite untouched. After-claim errors retain
    the stage/request barriers; the controller must reconcile rather than
    call this entrypoint again. Clients must disable SDK-level paid retries.
    """
    if expected_stage not in {"gemini_primary", "chirp_independent", "third_optional"}:
        raise V2Conflict("only an explicitly reviewed paid stage may dispatch")
    if (type(expected_revision) is not int or expected_revision < 0
            or not callable(verify_owner) or not callable(verify_capability_and_pricing)
            or not callable(read_metadata) or not isinstance(stage_options, dict)):
        raise V2Conflict("exact review state and trusted verifiers required")
    preview = preview_paid_dispatch(
        database=database, artifact_root=artifact_root, job_id=job_id,
        aggregate_trial_cap_cents=aggregate_cap_cents,
        expected_source_sha=expected_source_sha)
    if (preview["blockers"] or preview["job_revision"] != expected_revision
            or preview["stage"] != expected_stage):
        raise V2Conflict("paid stage is blocked or reviewed revision/stage changed")
    if (verify_owner(dict(preview)) is not True
            or verify_capability_and_pricing(dict(preview)) is not True):
        raise V2Conflict("independent owner/provider/pricing verification failed")
    # Perform the fresh authenticated source read AFTER potentially slow cloud
    # checks and immediately before the transactional revision/source claim.
    live = verify_live_drive_revision(
        database=database, artifact_root=artifact_root, job_id=job_id,
        read_metadata=read_metadata, max_approved_cents=aggregate_cap_cents)
    if (not live["live_drive_revision_verified"]
            or live["source_sha256"] != expected_source_sha
            or live["next_stage"] != expected_stage):
        raise V2Conflict("fresh Drive version or approved source cannot be verified")
    root = Path(artifact_root).resolve()
    job_dir = root / "jobs" / job_id
    preflight = json.loads((job_dir / "source-preflight.json").read_text())
    ledger = GoldenV2Ledger.open_existing(Path(database))
    fixed: dict[str, Any] = {
        "ledger": ledger, "claim": None,
        "normalized_audio": job_dir / "normalized.flac", "job_dir": job_dir,
    }
    if expected_stage != "chirp_independent":
        fixed.update(original_media=job_dir / "source.original",
                     drive_observation=preflight["drive_observation"])
    options = dict(stage_options)
    gateway = options.pop("gateway", None) if expected_stage == "chirp_independent" else None
    if fixed.keys() & options.keys():
        raise V2Conflict("controller may not replace verified source or claim identities")
    adapter = {"gemini_primary": execute_approved_primary_stage,
               "chirp_independent": plan_approved_chirp,
               "third_optional": execute_approved_third_stage}[expected_stage]
    try:
        inspect.signature(adapter).bind(**fixed, **options)
    except TypeError as exc:
        raise V2Conflict("stage service configuration is incomplete") from exc
    if expected_stage == "chirp_independent" and not callable(getattr(gateway, "submit", None)):
        raise V2Conflict("Chirp submission gateway missing")
    claim = ledger.claim_next(
        job_id, worker_id=worker_id, expected_stage=expected_stage,
        expected_revision=expected_revision, expected_source_sha=expected_source_sha,
        aggregate_cap_cents=aggregate_cap_cents)
    fixed["claim"] = claim
    result = adapter(**fixed, **options)
    if expected_stage == "chirp_independent":
        submit_planned_chirp(ledger=ledger, claim=claim, gateway=gateway)
    state = ledger.status(job_id)
    return {"job_id": job_id, "source_sha256": expected_source_sha,
            "stage": expected_stage, "status": state["status"],
            "revision": state["revision"],
            "live_drive_revision_verified": True,
            "drive_mutation_started": False,
            "result": result}
