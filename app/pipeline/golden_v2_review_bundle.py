"""Local, evidence-bound manual review handoff for V2 text disagreements.

This module packages exact comparison candidates for an operator to inspect
against the normalized audio, then imports explicit decisions without provider
or Drive calls. Reviewer identity and listening remain local operator claims.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.jobs.golden_v2_read import get_v2_status
from app.pipeline.golden_v2_stage_runner import (
    _bound_inputs, _canonical_sha, _correction_matches_candidate,
    complete_adjudication_or_hold,
)
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_versioned_artifacts import resolve_versioned
from app.subtitles.golden_v2_render import render_golden_v2

REVIEW_SCHEMA = "golden-v2-audio-review-bundle-v1"


def inspect_review_bundle(
    *, database: Path, job_id: str, job_dir: Path,
    normalized_audio: Path | None = None,
) -> dict[str, Any]:
    """Build a review template after validating immutable ledger evidence."""
    status = get_v2_status(database, job_id=job_id)
    if status is None:
        raise V2Conflict("unknown Golden V2 job")
    state = status["job"]
    stages = {row["name"]: row for row in status["stages"]}
    review_stage = stages.get("adjudication")
    if (state["status"] not in {"PROCESSING", "NEEDS_REVIEW"}
            or review_stage is None
            or review_stage["state"] not in {"PENDING", "REVIEW_REQUIRED"}):
        raise V2Conflict("job is not waiting for audio-grounded adjudication")
    if state["status"] == "NEEDS_REVIEW" and review_stage["state"] != "REVIEW_REQUIRED":
        raise V2Conflict("job review hold belongs to another stage")
    for name in ("gemini_primary", "chirp_independent", "compare"):
        if stages[name]["state"] != "COMPLETE":
            raise V2Conflict(f"required evidence stage is unresolved: {name}")

    unresolved_prior = [
        row["name"] for row in status["stages"]
        if row["ord"] < review_stage["ord"]
        and row["state"] not in {"COMPLETE", "SKIPPED"}
    ]
    if unresolved_prior and not (
        unresolved_prior == ["third_optional"]
        and state["mode"] == "FLASH_CHIRP"
        and stages["third_optional"]["state"] == "PENDING"
    ):
        raise V2Conflict("a prior stage is not complete: " + ", ".join(unresolved_prior))

    audio_path = Path(normalized_audio or (job_dir / "normalized.flac"))
    if not audio_path.is_file() or file_sha256(audio_path) != state["source_sha"]:
        raise V2Conflict("normalized audio differs from the approved source SHA")

    claim_stub = {"job_id": job_id, "source_sha": state["source_sha"]}
    _, merged, bound, _primary = _bound_inputs(
        ledger=GoldenV2Ledger.open_existing(database),
        claim=claim_stub, job_dir=job_dir,
    )
    duration_ms = merged.get("duration_ms")
    if type(duration_ms) is not int or duration_ms <= 0:
        raise V2Conflict("verified Chirp evidence has no valid source duration")
    comparison_path = resolve_versioned(
        job_dir / "golden-v2" / "engine-comparison.json",
        bound["compare"]["evidence_sha"],
    )
    try:
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V2Conflict("comparison artifact is missing or invalid") from exc
    compare_stage = bound["compare"]
    if (not isinstance(comparison, dict)
            or _canonical_sha(comparison) != compare_stage["evidence_sha"]
            or comparison.get("job_id") != job_id
            or comparison.get("source_sha256") != state["source_sha"]
            or comparison.get("primary_evidence_sha256") != bound["gemini_primary"]["evidence_sha"]
            or comparison.get("chirp_evidence_sha256") != bound["chirp_independent"]["evidence_sha"]
            or comparison.get("chirp_timing_authority") is not True):
        raise V2Conflict("comparison does not match verified provider evidence")
    candidates = comparison.get("candidates")
    if (not isinstance(candidates, list) or not candidates
            or comparison.get("pending_review_count") != len(candidates)):
        raise V2Conflict("there are no pending comparison candidates")
    candidate_snapshot = [
        {"candidate_index": index, "candidate_sha256": _canonical_sha(item),
         "evidence": item}
        for index, item in enumerate(candidates)
    ]
    previous_review_sha = None
    if review_stage["state"] == "REVIEW_REQUIRED":
        previous_review_sha = review_stage["evidence_sha"]

    return {
        "schema": REVIEW_SCHEMA,
        "job_id": job_id,
        "revision": state["revision"],
        "source_sha256": state["source_sha"],
        "normalized_audio_path": str(audio_path.resolve()),
        "normalized_audio_sha256": state["source_sha"],
        "comparison_evidence_sha256": compare_stage["evidence_sha"],
        "previous_review_evidence_sha256": previous_review_sha,
        "review_scope": "COMPARISON_CANDIDATES_ONLY",
        "whole_file_qa": "UNVERIFIED",
        "chirp_timing_authority": "chirp_3",
        "merged_duration_ms": duration_ms,
        "candidate_snapshot": candidate_snapshot,
        "review_decisions": [],
        "replacements": [],
        "punctuation_after": {},
        "operator_listening_attestation": "NOT_RECORDED",
    }


def _validate_bundle_snapshot(bundle: dict[str, Any], current: dict[str, Any]) -> None:
    for key in ("schema", "job_id", "revision", "source_sha256",
                "comparison_evidence_sha256", "candidate_snapshot"):
        if bundle.get(key) != current.get(key):
            raise V2Conflict(f"review bundle is stale or altered: {key}")
    if bundle.get("schema") != REVIEW_SCHEMA:
        raise V2Conflict("unrecognized review bundle schema")


def apply_review_bundle(
    *, database: Path, job_id: str, job_dir: Path, normalized_audio: Path,
    bundle: dict[str, Any], reviewer: str, review_reference: str,
    confirm_listened: bool,
) -> dict[str, Any]:
    """Record operator decisions after source, revision, and candidate checks."""
    if (confirm_listened is not True or not isinstance(reviewer, str)
            or not reviewer.strip() or len(reviewer) > 128
            or "\n" in reviewer or "\r" in reviewer
            or not isinstance(review_reference, str)
            or not review_reference.strip() or len(review_reference) > 256
            or "\n" in review_reference or "\r" in review_reference):
        raise ValueError("apply requires listening confirmation, reviewer, and reference")
    if not isinstance(bundle, dict):
        raise ValueError("review bundle must be a JSON object")
    current = inspect_review_bundle(
        database=database, job_id=job_id, job_dir=job_dir,
        normalized_audio=normalized_audio,
    )
    _validate_bundle_snapshot(bundle, current)
    if bundle.get("normalized_audio_sha256") != current["normalized_audio_sha256"]:
        raise V2Conflict("review bundle audio identity changed")

    candidate_by_index = {item["candidate_index"]: item for item in current["candidate_snapshot"]}
    decisions_in = bundle.get("review_decisions", [])
    replacements_in = bundle.get("replacements", [])
    punctuation_in = bundle.get("punctuation_after", {})
    if (not isinstance(decisions_in, list) or not isinstance(replacements_in, list)
            or not isinstance(punctuation_in, dict)):
        raise ValueError("decisions, replacements, and punctuation have invalid types")

    decisions: list[dict[str, Any]] = []
    seen: set[int] = set()
    corrected: set[int] = set()
    for entry in decisions_in:
        allowed = {"candidate_index", "status", "decision",
                   "reviewed_audio_window_ms", "decision_reason"}
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise ValueError("review decision contains unsupported fields")
        index = entry.get("candidate_index")
        if type(index) is not int or index not in candidate_by_index or index in seen:
            raise ValueError("review candidate index is unknown or duplicated")
        seen.add(index)
        decision = {
            "candidate_index": index,
            "candidate_sha256": candidate_by_index[index]["candidate_sha256"],
            "source_sha256": current["source_sha256"],
            "status": entry.get("status"),
            "reviewer": reviewer,
            "review_reference": review_reference,
        }
        reason = entry.get("decision_reason")
        if reason is not None:
            decision["decision_reason"] = reason
        if entry.get("status") == "RESOLVED":
            if entry.get("decision") not in {"KEEP_CHIRP", "CORRECT_CHIRP"}:
                raise ValueError("resolved decision must keep or correct Chirp")
            bounds = entry.get("reviewed_audio_window_ms")
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(value) is not int for value in bounds)
                    or not 0 <= bounds[0] < bounds[1]
                    or bounds[1] - bounds[0] > 120_000
                    or bounds[1] > current["merged_duration_ms"]):
                raise ValueError("review needs a valid audio range of at most 120 seconds")
            candidate = candidate_by_index[index]["evidence"]
            start_ms, end_ms = candidate.get("source_time_start"), candidate.get("source_time_end")
            if start_ms is not None or end_ms is not None:
                if (type(start_ms) is not int or type(end_ms) is not int
                        or not (bounds[0] <= start_ms <= end_ms <= bounds[1])):
                    raise ValueError("reviewed audio range misses the disputed source words")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("resolved candidate needs a decision reason")
            decision.update({
                "decision": entry["decision"],
                "audio_evidence_checked": True,
                "reviewed_audio_sha256": current["source_sha256"],
                "reviewed_audio_window_ms": bounds,
            })
            if entry["decision"] == "CORRECT_CHIRP":
                corrected.add(index)
        elif entry.get("status") == "REVIEW_REQUIRED":
            if entry.get("decision") is not None:
                raise ValueError("unresolved candidate cannot carry a final decision")
        else:
            raise ValueError("review status must be RESOLVED or REVIEW_REQUIRED")
        decisions.append(decision)

    replacements: list[dict[str, Any]] = []
    replacement_ids: set[int] = set()
    _, merged, _, _primary = _bound_inputs(
        ledger=GoldenV2Ledger.open_existing(database),
        claim={"job_id": job_id, "source_sha": current["source_sha256"]},
        job_dir=job_dir,
    )
    for patch in replacements_in:
        if not isinstance(patch, dict):
            raise ValueError("replacement must be an object")
        if set(patch) - {"candidate_index", "start_word", "end_word",
                         "corrected_text", "chirp_word_evidence_shas"}:
            raise ValueError("replacement contains unsupported fields")
        index = patch.get("candidate_index")
        if type(index) is not int or index not in corrected or index in replacement_ids:
            raise ValueError("replacement must map once to a CORRECT_CHIRP decision")
        reason = next(item["decision_reason"] for item in decisions
                      if item["candidate_index"] == index)
        correction = {
            **patch,
            "source_sha256": current["source_sha256"],
            "audio_evidence_checked": True,
            "decision_reason": reason,
        }
        if not _correction_matches_candidate(
            correction=correction,
            candidate=candidate_by_index[index]["evidence"],
            words=merged["words"], source_sha=current["source_sha256"],
        ):
            raise ValueError("replacement does not match source-bound Chirp words")
        replacement_ids.add(index)
        replacements.append(correction)
    if replacement_ids != corrected or len(replacements) != len(corrected):
        raise ValueError("every CORRECT_CHIRP decision needs exactly one word replacement")

    punctuation: dict[int, str] = {}
    for key, value in punctuation_in.items():
        try:
            index = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError("punctuation key must be a source word index") from exc
        if not isinstance(value, str):
            raise ValueError("punctuation value must be text")
        punctuation[index] = value
    # Validate cue ranges and replacements before creating a durable claim.
    render_golden_v2(merged=merged, replacements=replacements,
                     punctuation_after=punctuation)

    state = get_v2_status(database, job_id=job_id)
    if state is None or state["job"]["revision"] != current["revision"]:
        raise V2Conflict("job changed after review bundle validation")
    adjudication = next(row for row in state["stages"] if row["name"] == "adjudication")
    ledger = GoldenV2Ledger.open_existing(database)
    if adjudication["state"] == "REVIEW_REQUIRED":
        evidence_sha = adjudication["evidence_sha"]
        review_files = list((job_dir / "golden-v2").glob("adjudication-review-*.json"))
        if not any(file_sha256(path) == evidence_sha for path in review_files):
            raise V2Conflict("prior adjudication hold artifact is missing or changed")
        ledger.resume_adjudication_review(
            job_id, revision=current["revision"], reviewer=reviewer,
            review_reference=review_reference,
            previous_evidence_sha=evidence_sha,
        )
    claim = ledger.claim_next(
        job_id, worker_id=f"local-review:{reviewer}",
        expected_stage="adjudication",
    )
    result = complete_adjudication_or_hold(
        ledger=ledger, claim=claim, job_dir=job_dir,
        decisions=decisions, replacements=replacements,
        punctuation_after=punctuation,
        reviewer=reviewer, review_reference=review_reference,
    )
    return {
        **result,
        "operator_listening_attestation": "SELF_ATTESTED",
        "provider_calls_started": False,
        "drive_mutation_started": False,
    }
