"""Golden V2 provider-free durable stage bridge: compare → review/QA.

This is *not* the paid recognition runner and never touches Drive. Stage claims
come from the isolated SQLite ledger. Artifacts are immutable by content and
become resumable evidence once the stage transaction commits.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_acoustic_qa import inspect_v2_acoustic
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_compare import compare_gemini_chirp
from app.providers.golden_v2_final_gate import validate_golden_v2
from app.subtitles.golden_v2_render import render_golden_v2
from app.providers.golden_v2_versioned_artifacts import resolve_versioned, write_versioned
from app.pipeline.golden_v2_alignment_health import assess_alignment_health
from app.pipeline.golden_v2_review_evidence import require_real_review_evidence
from app.pipeline.golden_v2_chirp_first_reference import read_chirp_first_reference


def _write_evidence(path: Path, data: dict[str, Any]) -> str:
    """Keep historical evidence immutable across repaired rounds."""
    wire = json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()
    digest, _ = write_versioned(path, wire)
    return digest


def _canonical_sha(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _checked_stage_artifact(
    *, job_dir: Path, filename: str, stage: dict[str, Any],
    claim: dict[str, Any], raw_digest: bool = False,
) -> dict[str, Any]:
    path = resolve_versioned(job_dir / "golden-v2" / filename,
                             stage["evidence_sha"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V2Conflict(f"missing or invalid immutable {filename}") from exc
    if (not isinstance(data, dict) or stage["state"] != "COMPLETE"
            or stage["evidence_sha"] not in {file_sha256(path), _canonical_sha(data)}
            or data.get("job_id") != claim["job_id"]
            or data.get("source_sha256", data.get("source_sha")) != claim["source_sha"]):
        raise V2Conflict(f"ledger/source evidence mismatch: {filename}")
    return data


def _write_immutable_text(path: Path, content: str) -> None:
    write_versioned(path, content.encode("utf-8"))


def _primary_coverage_ratio(
    primary: dict[str, Any], duration_ms: int | None = None,
) -> float | None:
    """Fraction of the audio the reference text actually covers.

    Derived from the primary artifact's own window evidence: only windows
    that finished cleanly (STOP) contributed text. A window that was refused,
    truncated, or never attempted leaves its span uncovered, which is exactly
    the hole that made 251025 and 251018 unauditable.

    The denominator is the real audio duration when known, not the last
    window's end: 251025 planned windows ending at 5390s but the recording
    runs 8883.67s, so measuring against the window span would report 50%
    coverage when the true figure is 30.4%.
    """
    evidence = primary.get("window_evidence")
    if not isinstance(evidence, list) or not evidence:
        return None
    covered_ms = 0.0
    span_ms = 0.0
    for item in evidence:
        if not isinstance(item, dict):
            continue
        window = (item.get("identity") or {}).get("window") or {}
        start, end = window.get("start_seconds"), window.get("end_seconds")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if end <= start:
            continue
        span_ms = max(span_ms, float(end) * 1000.0)
        if item.get("finish_reason") == "STOP":
            covered_ms += (float(end) - float(start)) * 1000.0
    denominator = float(duration_ms) if isinstance(duration_ms, int) and duration_ms > 0 else span_ms
    if denominator <= 0:
        return None
    return covered_ms / denominator


def require_chirp_first_reference_ready(
    *, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
) -> None:
    """Preflight reference before claiming/reclaiming local compare."""
    state = ledger.status(job_id)
    stages = {row["name"]: row for row in state["stages"]}
    if stages["gemini_primary"]["state"] != "SKIPPED":
        return
    if stages["chirp_independent"]["state"] != "COMPLETE":
        raise V2Conflict("Chirp-first reference requires completed Chirp evidence")
    # Evidence lookup must use the immutable ledger SHA, not just a loose alias.
    claim = {"job_id": job_id, "source_sha": state["source_sha"]}
    merged = _checked_stage_artifact(
        job_dir=job_dir, filename="chirp-merged.json",
        stage=stages["chirp_independent"], claim=claim, raw_digest=True)
    if merged.get("timing_authority") != "chirp_3":
        raise V2Conflict("Chirp-first reference requires Chirp timing evidence")
    read_chirp_first_reference(
        job_dir=job_dir, job_id=job_id,
        source_sha=state["source_sha"], duration_ms=merged["duration_ms"])


def _bound_inputs(*, ledger: GoldenV2Ledger, claim: dict[str, Any],
                  job_dir: Path) -> tuple[str, dict[str, Any], dict[str, Any],
                                       dict[str, Any]]:
    """Use actual completed ledger artifacts as the sole comparison inputs."""
    stages = {item["name"]: item for item in ledger.status(claim["job_id"])["stages"]}
    if stages["gemini_primary"]["state"] == "SKIPPED":
        merged = _checked_stage_artifact(
            job_dir=job_dir, filename="chirp-merged.json",
            stage=stages["chirp_independent"], claim=claim, raw_digest=True)
        if merged.get("schema") != "golden-v2-chirp-merged-v1" or merged.get("timing_authority") != "chirp_3":
            raise V2Conflict("Chirp-first requires verified merged word timing")
        reference_text, reference = read_chirp_first_reference(
            job_dir=job_dir, job_id=claim["job_id"],
            source_sha=claim["source_sha"], duration_ms=merged["duration_ms"])
        return reference_text, merged, stages, reference
    original = job_dir / "golden-v2" / "primary-stage.json"
    # The paid stage may have completed directly or been promoted after an
    # audio-reviewed seam resolution. Match the immutable file to its ledger
    # SHA; an orphaned or forged review file cannot become input evidence.
    try:
        original_data = json.loads(original.read_text(encoding="utf-8"))
        original_sha = file_sha256(original)
    except (OSError, ValueError, TypeError) as exc:
        raise V2Conflict("original immutable primary evidence missing or invalid") from exc
    if (not isinstance(original_data, dict)
            or original_data.get("job_id") != claim["job_id"]
            or original_data.get("source_sha256") != claim["source_sha"]
            or original_data.get("schema") != "golden-v2-primary-stage-v1"):
        raise V2Conflict("original primary evidence source or job mismatch")
    primary_filename = "primary-stage.json"
    if original_sha != stages["gemini_primary"]["evidence_sha"]:
        primary_filename = (
            f"primary-stage-reviewed-{stages['gemini_primary']['evidence_sha']}.json"
        )
    primary = _checked_stage_artifact(
        job_dir=job_dir, filename=primary_filename,
        stage=stages["gemini_primary"], claim=claim)
    if primary_filename != "primary-stage.json":
        review = primary.get("audio_review")
        if (not isinstance(review, dict)
                or review.get("original_primary_evidence_sha256") != original_sha
                or review.get("review_scope") != "OVERLAP_SEAMS_ONLY"
                or review.get("whole_file_qa") != "UNVERIFIED"
                or original_data.get("ready_for_chirp") is not False):
            raise V2Conflict("reviewed primary is not bound to the original held evidence")
    merged = _checked_stage_artifact(
        job_dir=job_dir, filename="chirp-merged.json",
        stage=stages["chirp_independent"], claim=claim, raw_digest=True)
    assembled = primary.get("assembled") or {}
    text = assembled.get("text") if isinstance(assembled, dict) else None
    if (primary.get("schema") != "golden-v2-primary-stage-v1"
            or primary.get("ready_for_chirp") is not True
            or not isinstance(text, str) or not text.strip()
            or merged.get("schema") != "golden-v2-chirp-merged-v1"
            or merged.get("timing_authority") != "chirp_3"
            or not isinstance(merged.get("words"), list)):
        raise V2Conflict("complete source-bound primary and Chirp evidence required")
    return text, merged, stages, primary


def _correction_matches_candidate(
    *, correction: dict[str, Any], candidate: dict[str, Any],
    words: list[dict[str, Any]], source_sha: str,
) -> bool:
    from app.subtitles.golden_v2_render import _word_evidence
    start, end = correction.get("start_word"), correction.get("end_word")
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(words)):
        return False
    evidence_ids = [hashlib.sha256((
        f"{source_sha}|{i}|{words[i]['word']}|"
        f"{words[i]['start_ms']}|{words[i]['end_ms']}"
    ).encode()).hexdigest() for i in range(start, end)]
    return (bool(evidence_ids)
            and sorted(candidate.get("chirp_evidence_ids", [])) == sorted(evidence_ids)
            and correction.get("chirp_word_evidence_shas") == [
                _word_evidence(words[i], i, source_sha) for i in range(start, end)])


def complete_adjudication_or_hold(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], job_dir: Path,
    decisions: list[dict[str, Any]] | None = None,
    replacements: list[dict[str, Any]] | None = None,
    punctuation_after: dict[int, str] | None = None,
    reviewer: str = "", review_reference: str = "",
    validate_only: bool = False,
) -> dict[str, Any]:
    """Persist human audio review or an immutable hold without spending.

    The invoking owner-only interface MUST authenticate ``reviewer`` before
    calling this function. A reviewer's assertion is not automatic ASR truth.
    Each revision has its own hold artifact, retained across resumes.
    """
    if not validate_only:
        ledger.assert_stage_claim(claim, stage="adjudication")
    if file_sha256(job_dir / "normalized.flac") != claim["source_sha"]:
        raise V2Conflict("review audio SHA differs from approved normalized source")
    _, merged, stages, _primary = _bound_inputs(
        ledger=ledger, claim=claim, job_dir=job_dir)
    comparison = _checked_stage_artifact(
        job_dir=job_dir, filename="engine-comparison.json",
        stage=stages["compare"], claim=claim)
    if (comparison.get("primary_evidence_sha256") != stages["gemini_primary"]["evidence_sha"]
            or comparison.get("chirp_evidence_sha256") != stages["chirp_independent"]["evidence_sha"]):
        raise V2Conflict("adjudication comparison is not bound to provider evidence")
    candidates = comparison.get("candidates")
    if not isinstance(candidates, list) or comparison.get("pending_review_count") != len(candidates):
        raise V2Conflict("incomplete comparison evidence")
    decisions = decisions or []
    replacements = replacements or []
    punctuation_after = punctuation_after or {}
    if not isinstance(decisions, list) or not isinstance(replacements, list):
        raise V2Conflict("review decisions and replacements must be lists")
    if candidates and (not reviewer.strip() or not review_reference.strip()):
        raise V2Conflict("authenticated reviewer and audio review reference required")
    if not candidates and (decisions or replacements):
        raise V2Conflict("corrections require a matching comparison candidate")

    seen: set[int] = set()
    for entry in decisions:
        if not isinstance(entry, dict):
            raise V2Conflict("invalid adjudication entry")
        index = entry.get("candidate_index")
        if type(index) is not int or not 0 <= index < len(candidates) or index in seen:
            raise V2Conflict("adjudication candidate must be unique and bounded")
        seen.add(index)
        candidate = candidates[index]
        if (entry.get("candidate_sha256") != _canonical_sha(candidate)
                or entry.get("source_sha256") != claim["source_sha"]):
            raise V2Conflict("adjudication candidate/source mismatch")
        if entry.get("status") == "RESOLVED":
            bounds = entry.get("reviewed_audio_window_ms")
            if (entry.get("audio_evidence_checked") is not True
                    or entry.get("reviewed_audio_sha256") != claim["source_sha"]
                    or entry.get("reviewer") != reviewer
                    or entry.get("review_reference") != review_reference
                    or not str(entry.get("decision_reason") or "").strip()
                    or entry.get("decision") not in {"KEEP_CHIRP", "CORRECT_CHIRP"}
                    or not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(value) is not int for value in bounds)
                    or not 0 <= bounds[0] < bounds[1]
                    or bounds[1] - bounds[0] > 120_000):
                raise V2Conflict("resolved candidate needs bounded reviewed audio evidence")
            start = candidate.get("source_time_start")
            end = candidate.get("source_time_end")
            if start is not None and end is not None and not (bounds[0] <= start <= end <= bounds[1]):
                raise V2Conflict("reviewed audio window does not cover disputed source words")
            duration_ms = merged.get("duration_ms")
            if type(duration_ms) is int and bounds[1] > duration_ms:
                raise V2Conflict("reviewed audio window extends past normalized source")
        elif entry.get("status") != "REVIEW_REQUIRED":
            raise V2Conflict("unrecognized adjudication status")

    by_index = {entry["candidate_index"]: entry for entry in decisions}
    corrected_indices = {i for i, entry in by_index.items()
                         if entry.get("status") == "RESOLVED" and entry.get("decision") == "CORRECT_CHIRP"}
    if any(type(p.get("candidate_index")) is not int for p in replacements
           if isinstance(p, dict)) or any(not isinstance(p, dict) for p in replacements):
        raise V2Conflict("invalid corrected candidate index")
    if {patch["candidate_index"] for patch in replacements} != corrected_indices:
        raise V2Conflict("each corrected candidate requires exactly one source-word patch")
    if len(replacements) != len(corrected_indices):
        raise V2Conflict("duplicate source-word correction")
    # Validate the actual renderer's source-bound word hashes and cue layout
    # before a stage can reach COMPLETE; QA will independently re-validate.
    for patch in replacements:
        if (patch.get("source_sha256") != claim["source_sha"]
                or patch.get("audio_evidence_checked") is not True
                or not _correction_matches_candidate(
                    correction=patch, candidate=candidates[patch["candidate_index"]],
                    words=merged["words"], source_sha=claim["source_sha"])):
            raise V2Conflict("unaudited source-word correction")
    render_golden_v2(merged=merged, replacements=replacements,
                     punctuation_after=punctuation_after)

    # Review evidence gate: a batch whose windows and reasons are generated
    # rather than listened to must not become a completed adjudication.
    evidence = require_real_review_evidence(
        candidates=candidates, decisions=decisions)

    complete = len(seen) == len(candidates) and all(
        entry.get("status") == "RESOLVED" for entry in decisions)
    if not candidates and not comparison.get("exact_normalized_match"):
        raise V2Conflict("unmatched comparison cannot bypass audio review")
    if validate_only:
        return {'review_input_validated': True, 'provider_calls_started': False,
                'drive_mutation_started': False}
    revision = ledger.status(claim["job_id"])["revision"]
    report = {
        "schema": "golden-v2-adjudication-v1", "job_id": claim["job_id"],
        "source_sha256": claim["source_sha"],
        "comparison_sha256": stages["compare"]["evidence_sha"],
        "revision": revision, "reviewer": reviewer,
        "review_reference": review_reference,
        "adjudications": sorted(decisions, key=lambda item: item["candidate_index"]),
        "replacements": replacements,
        "punctuation_after": {str(k): v for k, v in punctuation_after.items()},
        "review_evidence": evidence,
        "status": "COMPLETE" if complete else "REVIEW_REQUIRED",
    }
    filename = "adjudications.json" if complete else f"adjudication-review-{revision}.json"
    digest = _write_evidence(job_dir / "golden-v2" / filename, report)
    if complete:
        ledger.complete(claim, evidence_sha=digest, actual_cents=0)
    else:
        ledger.hold_for_review(claim, evidence_sha=digest)
    return {**report, "evidence_sha256": digest, "provider_calls_started": False}


def complete_evidence_bound_qa(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], job_dir: Path,
    replacements: list[dict[str, Any]] | None = None,
    punctuation_after: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Finish V2 locally from verified stage artifacts, never caller-supplied PASS.

    A comparison disagreement requires persisted, source-bound, audio-reviewed
    adjudications. This bridge produces reviewable local output; no Drive write.
    """
    ledger.assert_stage_claim(claim, stage="golden_qa")
    status = ledger.status(claim["job_id"])
    stages = {row["name"]: row for row in status["stages"]}
    merged = _checked_stage_artifact(
        job_dir=job_dir, filename="chirp-merged.json",
        stage=stages["chirp_independent"], claim=claim, raw_digest=True)
    if (merged.get("schema") != "golden-v2-chirp-merged-v1"
            or file_sha256(job_dir / "normalized.flac") != claim["source_sha"]):
        raise V2Conflict("final QA source is not the verified normalized audio")
    comparison = _checked_stage_artifact(
        job_dir=job_dir, filename="engine-comparison.json",
        stage=stages["compare"], claim=claim)
    if (comparison.get("pending_review_count") != len(comparison.get("candidates", []))
            or comparison.get("chirp_timing_authority") is not True):
        raise V2Conflict("comparison evidence is incomplete")

    primary_text, checked_merged, bound_stages, bound_primary = _bound_inputs(
        ledger=ledger, claim=claim, job_dir=job_dir)
    expected = _build_comparison_report(
        primary_text=primary_text, merged=checked_merged, primary=bound_primary,
        claim=claim, stages=bound_stages)
    if expected != comparison:
        raise V2Conflict("persisted comparison differs from actual primary/Chirp evidence")

    adjudications: list[dict[str, Any]] = []
    record: dict[str, Any] = {}
    if comparison["candidates"] or replacements or punctuation_after:
        record = _checked_stage_artifact(
            job_dir=job_dir, filename="adjudications.json",
            stage=stages["adjudication"], claim=claim)
        adjudications = record.get("adjudications", [])
        if (not isinstance(adjudications, list)
                or any(not isinstance(item, dict) for item in adjudications)):
            raise V2Conflict("invalid audio-grounded adjudications")
        if (record.get("schema") != "golden-v2-adjudication-v1"
                or record.get("status") != "COMPLETE"
                or record.get("comparison_sha256") != stages["compare"]["evidence_sha"]):
            raise V2Conflict("unverified or stale adjudication evidence")

    candidate_ids = list(range(len(comparison["candidates"])))
    selected_ids = [item.get("candidate_index") for item in adjudications]
    evidence_resolved = (
        selected_ids == candidate_ids
        and all(item.get("status") == "RESOLVED"
                and item.get("audio_evidence_checked") is True
                and item.get("source_sha256") == claim["source_sha"]
                and item.get("candidate_sha256") == _canonical_sha(
                    comparison["candidates"][item["candidate_index"]])
                and item.get("reviewed_audio_sha256") == claim["source_sha"]
                and item.get("decision") in {"KEEP_CHIRP", "CORRECT_CHIRP"}
                and bool(str(item.get("reviewer") or "").strip())
                and bool(str(item.get("review_reference") or "").strip())
                and str(item.get("decision_reason") or "").strip()
                for item in adjudications)
    )
    semantic_gate = "PASS" if (
        not comparison.get("reference_requires_format_review", False)
        and (
            (not candidate_ids and comparison.get("exact_normalized_match") is True)
            or (candidate_ids and evidence_resolved)
        )
    ) else "REVIEW"
    trusted_replacements = record.get("replacements", [])
    trusted_punctuation = {int(k): v for k, v in record.get("punctuation_after", {}).items()}
    if replacements is not None and replacements != trusted_replacements:
        raise V2Conflict("caller changes persisted corrections")
    if punctuation_after is not None and punctuation_after != trusted_punctuation:
        raise V2Conflict("caller changes persisted punctuation")
    if trusted_replacements and not evidence_resolved:
        raise V2Conflict("correction requires verified source-bound adjudications")

    # Every edited source word must be tied to the reviewed candidate span.
    for correction in trusted_replacements:
        idx = correction.get("candidate_index")
        if type(idx) is not int or idx not in selected_ids:
            raise V2Conflict("correction has no resolved comparison candidate")
        candidate = comparison["candidates"][idx]
        if (adjudications[idx].get("decision") != "CORRECT_CHIRP"
                or not _correction_matches_candidate(
                    correction=correction, candidate=candidate,
                    words=merged["words"], source_sha=claim["source_sha"])):
            raise V2Conflict("correction is not limited to the reviewed Chirp span")
    if {i for i, entry in enumerate(adjudications)
            if entry.get("decision") == "CORRECT_CHIRP"} != {
                item["candidate_index"] for item in trusted_replacements}:
        raise V2Conflict("persisted audio review and correction decisions disagree")

    acoustic = inspect_v2_acoustic(job_dir=job_dir, merged=merged)
    acoustic_digest = _write_evidence(job_dir / "golden-v2" / "acoustic-qa.json", acoustic)
    rendered = render_golden_v2(
        merged=merged, replacements=trusted_replacements,
        punctuation_after=trusted_punctuation)
    render_manifest = {
        "schema": "golden-v2-render-manifest-v1", "job_id": claim["job_id"],
        "source_sha256": claim["source_sha"],
        "chirp_merged_sha256": stages["chirp_independent"]["evidence_sha"],
        "comparison_sha256": stages["compare"]["evidence_sha"],
        "acoustic_sha256": acoustic_digest,
        "adjudication_sha256": stages["adjudication"]["evidence_sha"] if adjudications else None,
        **{key: rendered[key] for key in (
            "cue_manifest", "srt_sha256", "transcript_sha256",
            "replacements_applied", "timing_authority")},
    }
    bounds = {boundary for word in merged["words"]
              for boundary in (word["start_ms"], word["end_ms"])}
    result = complete_qa_or_hold(
        ledger=ledger, claim=claim, job_dir=job_dir,
        srt=rendered["srt"], transcript=rendered["transcript"],
        chirp_word_boundaries_ms=bounds, acoustic_gate=acoustic["status"],
        semantic_gate=semantic_gate, comparison=comparison,
        adjudications=adjudications, commit=False)
    render_digest = None
    if result["safe_publish_allowed"]:
        final_dir = job_dir / "golden-v2"
        _write_immutable_text(final_dir / "final.srt", rendered["srt"])
        _write_immutable_text(final_dir / "final-corrected-transcript.txt", rendered["transcript"])
        render_digest = _write_evidence(final_dir / "render-manifest.json", render_manifest)
    # Bind the exact final manifest into the ledger-attested QA bytes. A later
    # publisher must verify this chain rather than accepting a loose PASS file.
    result['render_sha256'] = render_digest
    result['acoustic_sha256'] = acoustic_digest
    qa_digest = _write_evidence(job_dir / "golden-v2" / "golden-qa.json", result)
    if result["safe_publish_allowed"]:
        ledger.complete(claim, evidence_sha=qa_digest, actual_cents=0)
    else:
        ledger.hold_for_review(claim, evidence_sha=qa_digest)
    return {**result, "acoustic_sha256": acoustic_digest,
            "render_sha256": render_digest, "semantic_gate": semantic_gate,
            "acoustic_gate": acoustic["status"], "provider_calls_started": False,
            "drive_mutation_started": False}


def _build_comparison_report(
    *, primary_text: str, merged: dict[str, Any], primary: dict[str, Any],
    claim: dict[str, Any], stages: dict[str, Any],
) -> dict[str, Any]:
    """Build the comparison report and enforce the alignment health gate.

    Both the compare stage and the final QA stage build the comparison this
    way, so a report that QA accepts is byte-identical to the one the compare
    stage wrote. The gate is re-applied here deliberately: QA must not accept
    a comparison that the compare stage would now refuse.
    """
    report = compare_gemini_chirp(
        primary_text, merged["words"], source_sha256=claim["source_sha"])
    chirp_first = primary.get("schema") == "golden-v2-chirp-first-reference-v1"
    alignment = assess_alignment_health(
        candidates=report.get("candidates") or [],
        comparison=report,
        primary_coverage_ratio=(
            primary["reference_timeline_extent_ratio"] if chirp_first else
            _primary_coverage_ratio(primary, merged.get("duration_ms"))),
        duration_ms=merged.get("duration_ms"),
    )
    report["alignment_health"] = alignment
    if alignment["status"] != "PASS":
        raise V2Conflict(
            "comparison alignment is not trustworthy: "
            + ", ".join(alignment["reasons"]))
    report.update({
        "job_id": claim["job_id"],
        "source_sha256": claim["source_sha"],
        "primary_evidence_sha256": (
            None if chirp_first else stages["gemini_primary"]["evidence_sha"]),
        "reference_evidence_sha256": (
            primary["reference_evidence_sha256"] if chirp_first else None),
        "comparison_reference_kind": (
            "HUMAN_CORRECTED_SRT" if chirp_first else "GEMINI_PRIMARY"),
        "reference_format_issues": (
            primary.get("reference_format_issues", {}) if chirp_first else {}),
        "reference_requires_format_review": (
            primary.get("reference_requires_format_review", False)
            if chirp_first else False),
        "reference_is_acoustic_evidence": False,
        "chirp_evidence_sha256": stages["chirp_independent"]["evidence_sha"],
    })
    return report


def complete_comparison(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    job_dir: Path, primary_text: str | None = None,
    chirp_words: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ledger.assert_stage_claim(claim, stage="compare")
    verified_text, merged, stages, primary = _bound_inputs(
        ledger=ledger, claim=claim, job_dir=job_dir)
    if ((primary_text is not None and primary_text != verified_text)
            or (chirp_words is not None and chirp_words != merged["words"])):
        raise V2Conflict("caller comparison inputs differ from immutable provider artifacts")
    report = _build_comparison_report(
        primary_text=verified_text, merged=merged, primary=primary,
        claim=claim, stages=stages)
    digest = _write_evidence(job_dir / "golden-v2" / "engine-comparison.json", report)
    ledger.complete(
        claim, evidence_sha=digest, actual_cents=0,
        comparison_has_dispute=bool(report["pending_review_count"]),
    )
    return report


def complete_qa_or_hold(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    job_dir: Path, srt: str, transcript: str,
    chirp_word_boundaries_ms: set[int], acoustic_gate: str,
    semantic_gate: str, comparison: dict[str, Any],
    adjudications: list[dict[str, Any]], commit: bool = True,
) -> dict[str, Any]:
    if claim["stage"] != "golden_qa":
        raise V2Conflict("QA must own a golden_qa-stage claim")
    result = validate_golden_v2(
        srt=srt, transcript=transcript,
        chirp_word_boundaries_ms=chirp_word_boundaries_ms,
        acoustic_gate=acoustic_gate, semantic_gate=semantic_gate,
        comparison=comparison, adjudications=adjudications,
    )
    result["job_id"] = claim["job_id"]
    result["source_sha256"] = claim["source_sha"]
    if commit:
        digest = _write_evidence(job_dir / "golden-v2" / "golden-qa.json", result)
        if result["safe_publish_allowed"]:
            ledger.complete(claim, evidence_sha=digest, actual_cents=0)
        else:
            ledger.hold_for_review(claim, evidence_sha=digest)
    return result
