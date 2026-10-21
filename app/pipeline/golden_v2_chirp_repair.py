"""Plan, explicitly approve and stage bounded Standard Chirp repairs.

Proposals require positive local speech evidence. Exact source ranges, clip
hashes, GCS objects, acoustic assessment and a cent ceiling are bound to the
ledger before GCS upload. This module never submits a Speech request itself.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_pricing import standard_chirp_cents
from app.providers.golden_v2_audio_staging import (
    file_sha256, prepare_local_repair_clips,
)
from app.providers.golden_v2_gcs_staging import upload_approved_repair_clips
from app.providers.golden_v2_immutable import store_immutable_bytes


def _wire(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _safe_prefix(value: str) -> bool:
    return bool(value) and all(
        part not in {"", ".", ".."} and part.isascii()
        and all(ch.isalnum() or ch in "_-." for ch in part)
        for part in value.strip("/").split("/")
    ) and value == value.strip("/")


def verified_prior_chirp_evidence(job_dir: Path) -> Callable[..., bool]:
    """Fail-closed independent verifier for a completed stage's immutable bytes.

    The caller selects a trusted job_dir; supplied ledger metadata cannot
    redirect filesystem access. No provider, Drive or ledger writes occur.
    """
    root = Path(job_dir).resolve(strict=True)

    def verify(*, job_id: str, source_sha: str, old_chirp_sha: str,
               old_qa_sha: str, windows: list[dict[str, Any]]) -> bool:
        if root.name != job_id or not windows:
            return False
        stage = root / "golden-v2"
        try:
            if (file_sha256(root / "normalized.flac") != source_sha
                    or file_sha256(stage / "chirp-merged.json") != old_chirp_sha
                    or file_sha256(stage / "golden-qa.json") != old_qa_sha):
                return False
            lineage = json.loads((stage / "source-lineage.json").read_bytes())
            if lineage.get("normalized_sha256") != source_sha:
                return False
            for row in windows:
                if (row["job_id"] != job_id or row["source_sha"] != source_sha
                        or row["status"] != "DONE"
                        or row["actual_cents"] is None
                        or not row["operation_name"]
                        or row["purpose"] not in {"BASE", "REPAIR"}):
                    return False
                folder = stage / "chirp-evidence" / row["window_id"]
                raw = folder / "provider-raw.json"
                meta = json.loads((folder / "window.json").read_bytes())
                if (file_sha256(raw) != row["result_sha"]
                        or meta["raw_sha"] != row["result_sha"]
                        or meta["source_sha"] != source_sha
                        or meta["clip_sha"] != row["clip_sha"]
                        or meta["operation_name"] != row["operation_name"]
                        or meta["request_id"] != row["request_id"]
                        or meta["start_ms"] != row["start_ms"]
                        or meta["end_ms"] != row["end_ms"]
                        or meta["actual_cents"] != row["actual_cents"]):
                    return False
            return True
        except (OSError, KeyError, ValueError, TypeError):
            return False

    return verify


def _candidate_for_assessment(job_dir: Path, expected_sha: str) -> Path:
    for path in sorted((Path(job_dir) / "golden-v2").glob("chirp-merged-candidate-*.json")):
        try:
            value = json.loads(path.read_bytes())
            digest = hashlib.sha256(_wire(value)).hexdigest()
            if digest == expected_sha:
                return path
        except (OSError, ValueError):
            continue
    raise V2Conflict("acoustic assessment has no matching immutable Chirp candidate")


def propose_standard_repair_plan(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], job_dir: Path,
    normalized_audio: Path, acoustic_report_path: Path,
    bucket_name: str, object_prefix: str,
    estimate_window_cents: Callable[[dict[str, Any]], int],
    max_intervals: int = 12, max_total_ms: int = 600_000,
) -> dict[str, Any]:
    """Create a local-only proposal; no owner approval, upload or provider call."""
    ledger.assert_stage_claim(claim, stage="chirp_independent")
    if (not callable(estimate_window_cents) or not bucket_name
            or "/" in bucket_name or not _safe_prefix(object_prefix)):
        raise V2Conflict("repair proposal needs a cost estimator and safe private GCS prefix")
    try:
        acoustic = json.loads(Path(acoustic_report_path).read_bytes())
    except (OSError, ValueError) as exc:
        raise V2Conflict("immutable acoustic QA report is required") from exc
    assessment_sha = file_sha256(Path(acoustic_report_path))
    if (acoustic.get("schema") != "golden-v2-acoustic-qa-v1"
            or acoustic.get("status") != "REVIEW"
            or acoustic.get("source_sha256") != claim["source_sha"]):
        raise V2Conflict("repair needs a matching Chirp acoustic REVIEW")
    candidate_path = _candidate_for_assessment(job_dir, acoustic.get("merged_sha256", ""))
    candidate = json.loads(candidate_path.read_bytes())
    if (candidate.get("source_sha") != claim["source_sha"]
            or candidate.get("timing_authority") != "chirp_3"):
        raise V2Conflict("repair candidate source or timing authority mismatch")
    repair_plan = acoustic.get("repair_plan")
    proposals = repair_plan.get("intervals") if isinstance(repair_plan, dict) else None
    if (not isinstance(proposals, list) or not proposals or repair_plan.get("submit_authorized") is not False
            or repair_plan.get("strategy") != "STANDARD_BATCH"):
        raise V2Conflict("no bounded Standard repair proposal is available")
    gaps = acoustic.get("gaps_examined")
    if not isinstance(gaps, list):
        raise V2Conflict("acoustic report is missing classified gaps")
    for item in proposals:
        targets = item.get("target_intervals") if isinstance(item, dict) else None
        if not isinstance(targets, list) or not targets:
            raise V2Conflict("repair proposal is missing exact target intervals")
        for target in targets:
            index = target.get("gap_index") if isinstance(target, dict) else None
            if (type(index) is not int or not 0 <= index < len(gaps)
                    or gaps[index].get("disposition") != "SPEECH_CONFIRMED"
                    or target.get("start_ms", -1) < gaps[index].get("start_ms", 0)
                    or target.get("end_ms", 0) > gaps[index].get("end_ms", -1)):
                raise V2Conflict("repair target lacks matching positive speech evidence")

    local_dir = Path(job_dir) / "golden-v2" / "chirp-repair-clips"
    local_manifest = prepare_local_repair_clips(
        source=normalized_audio, expected_source_sha256=claim["source_sha"],
        output_dir=local_dir, intervals=proposals,
        max_intervals=max_intervals, max_total_ms=max_total_ms,
    )
    prior = [row for row in ledger.chirp_windows(claim["job_id"])
             if row.get("purpose") == "REPAIR"]
    rounds = [int(match.group(1)) for row in prior
              if (match := re.match(r"^repair-r(\d+)-", row["window_id"]))]
    round_id = max(rounds, default=0) + 1
    rows = []
    for item in local_manifest["windows"]:
        index = int(item["index"])
        window_id = f"repair-r{round_id:02d}-{index:02d}"
        estimate_item = {
            "window_id": window_id, "source_sha": claim["source_sha"],
            "clip_sha": item["clip_sha256"], "start_ms": item["start_ms"],
            "end_ms": item["end_ms"], "target_intervals": item["target_intervals"],
            "strategy": "STANDARD_BATCH",
        }
        cents = estimate_window_cents(estimate_item)
        if type(cents) is not int or cents < 0:
            raise V2Conflict("repair cost estimates must be non-negative integer cents")
        # Never reserve Dynamic Batch pricing for Standard Batch repair.
        cents = max(cents, standard_chirp_cents(
            (item["end_ms"] - item["start_ms"]) / 1000))
        key = (f"{object_prefix}/{claim['job_id']}/{claim['source_sha']}/"
               f"chirp-repair-inputs/{window_id}-{item['clip_sha256']}.flac")
        output = (f"gs://{bucket_name}/{object_prefix}/{claim['job_id']}/"
                  f"{claim['source_sha']}/chirp-repair-outputs/{window_id}-"
                  f"{item['clip_sha256'][:24]}/")
        row = {
            **estimate_item, "gcs_uri": f"gs://{bucket_name}/{key}",
            "output_uri": output, "estimated_cents": cents,
            "assessment_sha": assessment_sha,
        }
        item["window_id"] = window_id
        rows.append(row)
    if sum(row["estimated_cents"] for row in rows) <= 0:
        raise V2Conflict("repair estimate must reserve positive spend")
    proposal = {
        "schema": "golden-v2-standard-repair-proposal-v1",
        "job_id": claim["job_id"], "source_sha": claim["source_sha"],
        "candidate_sha": acoustic["merged_sha256"],
        "assessment_sha": assessment_sha, "repair_round": round_id,
        "strategy": "STANDARD_BATCH", "submit_authorized": False,
        "window_rows": rows, "estimated_cents": sum(r["estimated_cents"] for r in rows),
        "local_manifest_sha256": hashlib.sha256(_wire(local_manifest)).hexdigest(),
        "local_manifest": local_manifest,
    }
    proposal_sha = hashlib.sha256(_wire(proposal)).hexdigest()
    proposal_path = (Path(job_dir) / "golden-v2" /
                     f"chirp-standard-repair-proposal-{proposal_sha[:20]}.json")
    store_immutable_bytes(proposal_path, _wire(proposal))
    return {**proposal, "proposal_sha256": proposal_sha,
            "proposal_path": str(proposal_path), "local_dir": str(local_dir)}


def approve_and_stage_standard_repairs(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], proposal: dict[str, Any],
    approved_cents: int, approval_reference: str, approving_actor: str,
    bucket_name: str, object_prefix: str, storage_client: Any,
) -> dict[str, Any]:
    """Record exact approval, atomically commit operations, then upload clips."""
    ledger.assert_stage_claim(claim, stage="chirp_independent")
    path = Path(str(proposal.get("proposal_path", "")))
    if (not path.is_file() or file_sha256(path) != proposal.get("proposal_sha256")):
        raise V2Conflict("repair proposal file is missing or changed")
    saved = json.loads(path.read_bytes())
    if (saved.get("schema") != "golden-v2-standard-repair-proposal-v1"
            or saved.get("job_id") != claim["job_id"]
            or saved.get("source_sha") != claim["source_sha"]
            or saved.get("submit_authorized") is not False
            or hashlib.sha256(_wire(saved)).hexdigest() != proposal["proposal_sha256"]):
        raise V2Conflict("repair proposal is not bound to this approved Chirp claim")
    local_manifest = saved.get("local_manifest")
    if (not isinstance(local_manifest, dict)
            or hashlib.sha256(_wire(local_manifest)).hexdigest()
               != saved.get("local_manifest_sha256")):
        raise V2Conflict("local repair clip manifest differs from proposal")
    state = ledger.status(claim["job_id"])
    approval = ledger.approve_chirp_repair_plan(
        claim["job_id"], expected_revision=state["revision"],
        assessment_sha=saved["assessment_sha"], windows=saved["window_rows"],
        approved_cents=approved_cents,
        reference=approval_reference, actor=approving_actor,
    )
    ledger.plan_chirp_repair_windows(
        claim, plan_sha=approval["plan_sha"], windows=saved["window_rows"],
    )
    uploaded = upload_committed_standard_repair_clips(
        ledger=ledger, claim=claim, proposal=proposal,
        bucket_name=bucket_name, object_prefix=object_prefix,
        storage_client=storage_client,
    )
    return {
        "job_id": claim["job_id"], "plan_sha": approval["plan_sha"],
        "assessment_sha": saved["assessment_sha"],
        "approved_cents": approved_cents, "estimated_cents": saved["estimated_cents"],
        "status": "STANDARD_REPAIR_STAGED",
        "provider_submissions": 0, "drive_mutations": 0,
        "uploaded_windows": uploaded["uploaded_windows"],
    }


def upload_committed_standard_repair_clips(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], proposal: dict[str, Any],
    bucket_name: str, object_prefix: str, storage_client: Any,
) -> dict[str, Any]:
    """Retry idempotent GCS staging after a crash without replaying approval."""
    ledger.assert_stage_claim(claim, stage="chirp_independent")
    path = Path(str(proposal.get("proposal_path", "")))
    if (not path.is_file() or file_sha256(path) != proposal.get("proposal_sha256")):
        raise V2Conflict("repair proposal file is missing or changed")
    saved = json.loads(path.read_bytes())
    if (saved.get("schema") != "golden-v2-standard-repair-proposal-v1"
            or saved.get("job_id") != claim["job_id"]
            or saved.get("source_sha") != claim["source_sha"]
            or hashlib.sha256(_wire(saved)).hexdigest() != proposal["proposal_sha256"]):
        raise V2Conflict("repair proposal differs from its immutable identity")
    local_manifest = saved.get("local_manifest")
    if (not isinstance(local_manifest, dict)
            or hashlib.sha256(_wire(local_manifest)).hexdigest()
               != saved.get("local_manifest_sha256")):
        raise V2Conflict("local repair clip manifest differs from proposal")
    uploaded = upload_approved_repair_clips(
        ledger=ledger, claim=claim, local_manifest=local_manifest,
        local_dir=Path(proposal.get("local_dir", path.parent / "chirp-repair-clips")),
        bucket_name=bucket_name,
        object_prefix=object_prefix, storage_client=storage_client,
    )
    return {"job_id": claim["job_id"], "uploaded_windows": len(uploaded),
            "provider_submissions": 0, "drive_mutations": 0}


def resume_committed_standard_repair_uploads(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], job_dir: Path,
    normalized_audio: Path, bucket_name: str, object_prefix: str,
    storage_client: Any, max_intervals: int = 12,
    max_total_ms: int = 600_000,
) -> dict[str, Any]:
    """Rebuild exact local clips from the ledger after interrupted GCS staging."""
    ledger.assert_stage_claim(claim, stage="chirp_independent")
    rows = [row for row in ledger.chirp_windows(claim["job_id"])
            if row.get("purpose") == "REPAIR" and row["status"] == "PLANNED"]
    rows.sort(key=lambda row: row["window_id"])
    if (not rows or len(rows) > max_intervals
            or any(not re.match(r"^repair-r\d+-\d{2,}$", row["window_id"])
                   for row in rows)):
        raise V2Conflict("no bounded planned repair clips are safe to resume")
    intervals = [{
        "start_ms": row["start_ms"], "end_ms": row["end_ms"],
        "target_intervals": json.loads(row["target_intervals_json"]),
    } for row in rows]
    local_manifest = prepare_local_repair_clips(
        source=normalized_audio, expected_source_sha256=claim["source_sha"],
        output_dir=Path(job_dir) / "golden-v2" / "chirp-repair-clips",
        intervals=intervals, max_intervals=max_intervals,
        max_total_ms=max_total_ms,
    )
    for item, row in zip(local_manifest["windows"], rows):
        if (item["clip_sha256"] != row["clip_sha"]
                or item["start_ms"] != row["start_ms"]
                or item["end_ms"] != row["end_ms"]):
            raise V2Conflict("reconstructed repair clip differs from ledger identity")
        item["window_id"] = row["window_id"]
    uploaded = upload_approved_repair_clips(
        ledger=ledger, claim=claim, local_manifest=local_manifest,
        local_dir=Path(job_dir) / "golden-v2" / "chirp-repair-clips",
        bucket_name=bucket_name, object_prefix=object_prefix,
        storage_client=storage_client,
    )
    return {"job_id": claim["job_id"], "uploaded_windows": len(uploaded),
            "provider_submissions": 0, "drive_mutations": 0}


def submit_approved_standard_repairs(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], gateway: Any,
) -> list[dict[str, Any]]:
    """Submit only explicitly approved repair rows with Standard processing."""
    rows = ledger.chirp_windows(claim["job_id"])
    repairs = [row for row in rows if row.get("purpose") == "REPAIR"]
    if (not repairs or any(row["status"] not in {"PLANNED", "SUBMITTED", "DONE"}
                           for row in repairs)
            or any(row["status"] != "DONE" for row in rows if row.get("purpose") != "REPAIR")):
        raise V2Conflict("Standard repair requires completed base operations and a committed repair plan")
    if getattr(gateway, "dynamic_batching", None) is not False:
        raise V2Conflict("Standard repair gateway must disable dynamic batching")
    from app.pipeline.golden_v2_chirp_stage import submit_planned_chirp
    return [row for row in submit_planned_chirp(
        ledger=ledger, claim=claim, gateway=gateway)
            if row.get("purpose") == "REPAIR"]
