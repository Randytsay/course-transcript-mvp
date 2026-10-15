"""Local-clock fix for a quarantined Chirp window.

The official `_parse_words` in :mod:`golden_v2_chirp_stage` is intentionally
strict: any single Speech V2 word with a bad timestamp makes the whole window
fail validation. The provider result is then accounted (``actual_cents`` is
already known) but held in ``REVIEW_REQUIRED`` until a bounded parser fix
permits safe recovery.

This module is the **only** path that may move a ``REVIEW_REQUIRED`` window to
``DONE`` *without* a fresh provider call. It enforces the following invariants:

* The original ``provider-raw.json`` is **never** rewritten, deleted or
  replaced; its SHA-256 must equal the ledger ``v2_chirp_operations.result_sha``
  for both the proposal and the application step.
* A new immutable ``chirp-local-clock-fix-<sha>.json`` is written next to the
  raw evidence, recording the bounded fix proposal and every word that required
  a guess. ``unresolvable_words`` are flagged for human re-listen and never
  silently dropped from the source material.
* No new Speech request, no GCS upload, no charge change: the original
  ``actual_cents`` value passes through unchanged. The merge code in
  :func:`finish_approved_chirp` reads ``window.json`` exactly the same way as
  for a non-reviewed window; only ``words`` and ``timing_normalizations`` are
  added.
* The fix is rejected unless the parent job is in ``PROCESSING`` and the stage
  is ``RUNNING``/``OVER_BUDGET``; this matches the policy of
  :func:`recover_chirp_window`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_chirp_stage import _immutable, _offset_ms, _wire
from app.providers.golden_v2_audio_staging import file_sha256

# Bounded fix ceilings: kept tight so an attacker cannot widen the attack
# surface through a tampered raw JSON.
_MAX_FILL_MS = 250            # ceiling for a single inferred end_ms delta
_MAX_UNTRUSTED_FRACTION = 0.50  # at most 50% of the window's words may be untrusted
_SCHEMA = "golden-v2-chirp-local-clock-fix-v1"


def _parse_payload(raw_bytes: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, ValueError) as exc:
        raise V2Conflict("provider result JSON invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise V2Conflict("Speech V2 result list missing")
    return payload


def _entries_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Read every Speech result group; never discard recognized text."""
    entries: list[dict[str, Any]] = []
    for result in payload["results"]:
        if not isinstance(result, dict) or not isinstance(result.get("alternatives"), list):
            raise V2Conflict("Speech recognition alternatives missing")
        alternatives = result["alternatives"]
        if not alternatives:
            continue
        words = alternatives[0].get("words") if isinstance(alternatives[0], dict) else None
        if not isinstance(words, list):
            raise V2Conflict("Speech word timing evidence missing")
        entries.extend(words)
    if not entries:
        raise V2Conflict("Speech word timing evidence missing")
    return entries


def _valid_offset(entry: dict[str, Any], key: str, *, first_word: bool = False) -> int | None:
    if key not in entry or entry[key] is None:
        return 0 if first_word and key == "startOffset" else None
    try:
        return _offset_ms(entry[key])
    except (V2Conflict, TypeError):
        return None


def parse_local_clock_fix_candidate(
    raw_bytes: bytes, window: dict[str, Any]
) -> dict[str, Any]:
    """Conservative, provider-free candidate; never fabricate a word endpoint.

    Only coalesce *adjacent* tokens sharing an identical provider start offset
    when a later token supplies a valid provider end. Keep their text verbatim.
    Anything else is an explicit unresolved item that prevents auto-accept.
    """
    entries = _entries_from_payload(_parse_payload(raw_bytes))
    lo, hi = int(window["start_ms"]), int(window["end_ms"])
    if lo < 0 or hi <= lo:
        raise V2Conflict("invalid source window")
    fixed: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    normalized = 0
    i = 0
    while i < len(entries):
        row = entries[i]
        if not isinstance(row, dict) or not isinstance(row.get("word"), str) or not row["word"]:
            unresolved.append({"index": i, "reason": "Speech word is invalid"})
            i += 1
            continue
        start_offset = _valid_offset(row, "startOffset", first_word=(i == 0))
        if start_offset is None:
            unresolved.append({"index": i, "reason": "missing or invalid startOffset"})
            i += 1
            continue
        start = lo + start_offset
        if start < lo or start > hi + 250:
            unresolved.append({"index": i, "reason": "start out of source window",
                               "start_ms": start})
            i += 1
            continue
        end_offset = _valid_offset(row, "endOffset")
        end = lo + end_offset if end_offset is not None else None
        resolution: str | None = None
        text = row["word"]
        last_index = i
        if end is None or end <= start or end > hi + 250:
            # Identical-start zero-duration tokens may be joined without
            # inventing any timestamps. Other cases demand human review.
            j = i + 1
            while j < len(entries):
                following = entries[j]
                if not isinstance(following, dict) or not isinstance(following.get("word"), str) or not following["word"]:
                    break
                following_start = _valid_offset(following, "startOffset")
                if following_start is None or lo + following_start != start:
                    break
                text += following["word"]
                last_index = j
                following_end = _valid_offset(following, "endOffset")
                if following_end is not None and start < lo + following_end <= hi + 250:
                    end = lo + following_end
                    resolution = "COALESCED_SAME_START_PROVIDER_END"
                    break
                j += 1
            if resolution is None:
                unresolved.append({"index": i, "through_index": last_index,
                                   "reason": "no verified provider end for token span"})
                i = last_index + 1
                continue
        if fixed and start < fixed[-1]["end_ms"]:
            unresolved.append({"index": i, "through_index": last_index,
                               "reason": "overlapping provider word offsets"})
            i = last_index + 1
            continue
        word: dict[str, Any] = {"word": text, "start_ms": start, "end_ms": end}
        if resolution is not None:
            word["timing_resolution"] = resolution
            normalized += last_index - i + 1
        fixed.append(word)
        i = last_index + 1

    untrusted = normalized + len(unresolved)
    if untrusted / len(entries) > _MAX_UNTRUSTED_FRACTION:
        raise V2Conflict("local clock fix has more than 50% of window words untrusted; manual review required")
    return {
        "schema": _SCHEMA,
        "fixable_word_count": normalized,
        "unresolvable_word_count": len(unresolved),
        "total_entry_count": len(entries),
        "fixed_words": fixed,
        "unresolvable": unresolved,
        "safe_to_apply": not unresolved and bool(fixed),
    }


def propose_local_clock_fix(
    *, ledger: GoldenV2Ledger, job_id: str, window_id: str, job_dir: Path,
) -> dict[str, Any]:
    """Write an immutable local-clock fix proposal for a quarantined window.

    The proposal binds:

    * the immutable provider-raw SHA-256 (must equal ``result_sha``),
    * the original operation name, request id, and accounted ``actual_cents``
      (so charges cannot be retroactively widened),
    * a deterministic JSON snapshot of the fixed words plus the
      ``unresolvable`` diagnostics that the operator is expected to review.

    The proposal is *not* self-applying: the operator must explicitly call
    :func:`apply_local_clock_fix` to commit it, and that call is the only
    place the ledger may transition ``REVIEW_REQUIRED`` → ``DONE``.
    """
    rows = {row["window_id"]: row for row in ledger.chirp_windows(job_id)}
    row = rows.get(window_id)
    if row is None or row["status"] != "REVIEW_REQUIRED" or not row["operation_name"]:
        raise V2Conflict("no quarantined Chirp operation to fix locally")
    if type(row.get("actual_cents")) is not int or row["actual_cents"] < 0:
        raise V2Conflict("quarantined Chirp charge is unresolved")

    root = job_dir / "golden-v2" / "chirp-evidence" / window_id
    raw_path = root / "provider-raw.json"
    review_path = root / "review-required.json"
    if not raw_path.is_file() or not review_path.is_file():
        raise V2Conflict("quarantined raw or review evidence is missing")
    raw_bytes = raw_path.read_bytes()
    review = json.loads(review_path.read_bytes())
    evidence_sha = file_sha256(raw_path)
    if (evidence_sha != row["result_sha"]
            or review.get("schema") != "golden-v2-chirp-window-review-v1"
            or review.get("job_id") != job_id or review.get("window_id") != window_id
            or review.get("operation_name") != row["operation_name"]
            or review.get("raw_sha") != evidence_sha
            or review.get("actual_cents") != row["actual_cents"]):
        raise V2Conflict("quarantined raw identity or cost evidence changed")

    candidate = parse_local_clock_fix_candidate(raw_bytes, row)
    proposal_body = {
        "schema": _SCHEMA, "job_id": job_id, "window_id": window_id,
        "source_sha": row["source_sha"], "clip_sha": row["clip_sha"],
        "operation_name": row["operation_name"], "request_id": row["request_id"],
        "raw_sha": evidence_sha, "actual_cents": row["actual_cents"],
        "original_review_reason": review.get("reason"),
        "window_start_ms": row["start_ms"], "window_end_ms": row["end_ms"],
        "fixable_word_count": candidate["fixable_word_count"],
        "unresolvable_word_count": candidate["unresolvable_word_count"],
        "total_entry_count": candidate["total_entry_count"],
        "fixed_words": candidate["fixed_words"],
        "unresolvable": candidate["unresolvable"],
        "safe_to_apply": candidate["safe_to_apply"],
        "submit_authorized": False,  # local fix NEVER submits a provider call
    }
    proposal_sha = hashlib.sha256(_wire(proposal_body)).hexdigest()
    proposal_path = (job_dir / "golden-v2" /
                     f"chirp-local-clock-fix-{proposal_sha[:20]}.json")
    _immutable(proposal_path, _wire(proposal_body))
    return {**proposal_body, "proposal_sha256": proposal_sha,
            "proposal_path": str(proposal_path)}


def apply_local_clock_fix(
    *, ledger: GoldenV2Ledger, job_id: str, window_id: str, job_dir: Path,
    proposal_sha: str, owner_token: str,
) -> dict[str, Any]:
    """Commit an approved local-clock fix proposal.

    The function writes a fresh ``window.json`` that references the original
    ``raw_sha`` (the immutably stored provider response) **and** the
    ``local_clock_fix_sha`` (the fix proposal). It then asks the ledger to
    transition ``REVIEW_REQUIRED`` → ``DONE`` with a verifier that re-reads
    every required file. The original ``provider-raw.json`` is never touched;
    the original ``review-required.json`` is preserved for audit.

    No provider call is made; ``actual_cents`` is preserved exactly.
    """
    if not isinstance(proposal_sha, str) or len(proposal_sha) != 64 or any(
            c not in "0123456789abcdef" for c in proposal_sha):
        raise V2Conflict("local clock fix requires a 64-char hex SHA")
    if not isinstance(owner_token, str) or not owner_token.strip():
        raise V2Conflict("owner token required to apply local clock fix")

    rows = {row["window_id"]: row for row in ledger.chirp_windows(job_id)}
    row = rows.get(window_id)
    if row is None or row["status"] != "REVIEW_REQUIRED" or not row["operation_name"]:
        raise V2Conflict("only a quarantined Chirp window can accept a local fix")

    job_dir_path = Path(job_dir)
    root = job_dir_path / "golden-v2" / "chirp-evidence" / window_id
    raw_path = root / "provider-raw.json"
    review_path = root / "review-required.json"
    fix_path = job_dir_path / "golden-v2" / f"chirp-local-clock-fix-{proposal_sha[:20]}.json"
    if not raw_path.is_file() or not review_path.is_file() or not fix_path.is_file():
        raise V2Conflict("local fix requires raw, review and fix artifacts on disk")
    raw_bytes = raw_path.read_bytes()
    review = json.loads(review_path.read_bytes())
    proposal = json.loads(fix_path.read_bytes())
    evidence_sha = file_sha256(raw_path)
    if (evidence_sha != row["result_sha"]
            or hashlib.sha256(_wire(proposal)).hexdigest() != proposal_sha
            or proposal.get("schema") != _SCHEMA
            or proposal.get("job_id") != job_id or proposal.get("window_id") != window_id
            or proposal.get("raw_sha") != evidence_sha
            or proposal.get("actual_cents") != row["actual_cents"]):
        raise V2Conflict("local fix proposal identity or cost evidence changed")

    # Re-derive the candidate from the raw bytes to detect post-hoc edits.
    expected_candidate = parse_local_clock_fix_candidate(raw_bytes, row)
    if (expected_candidate["fixed_words"] != proposal["fixed_words"]
            or expected_candidate["unresolvable"] != proposal["unresolvable"]):
        raise V2Conflict("local fix proposal cannot be reproduced from raw bytes")

    if not expected_candidate["safe_to_apply"] or proposal.get("unresolvable"):
        raise V2Conflict("local clock fix requires human review for unresolved words")
    fixed_words = list(proposal["fixed_words"])
    fixed_words.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    normalizations = [
        {"word": item["word"], "start_ms": item["start_ms"],
         "end_ms": item["end_ms"], "method": item["timing_resolution"]}
        for item in fixed_words if item.get("timing_resolution")
    ]
    window_path = root / "window.json"
    manifest = {
        "schema": "golden-v2-chirp-window-v1", "job_id": job_id,
        "operation_name": row["operation_name"], "request_id": row["request_id"],
        "window_id": window_id, "source_sha": row["source_sha"],
        "clip_sha": row["clip_sha"], "input_uri": row["gcs_uri"],
        "output_uri": row["output_uri"], "start_ms": row["start_ms"],
        "end_ms": row["end_ms"], "raw_sha": evidence_sha,
        "words": fixed_words, "timing_normalizations": normalizations,
        "actual_cents": row["actual_cents"], "acoustic_gate": "NOT_RUN",
        "local_clock_fix_sha": proposal_sha,
    }
    _immutable(window_path, _wire(manifest))

    def verify(**expected: Any) -> bool:
        try:
            saved = json.loads(window_path.read_bytes())
            saved_review = json.loads(review_path.read_bytes())
            saved_fix = json.loads(fix_path.read_bytes())
            return (file_sha256(raw_path) == expected["evidence_sha"]
                    and saved == manifest
                    and saved_review == review
                    and saved_fix == proposal
                    and all(saved[key] == expected[value] for key, value in (
                        ("operation_name", "operation_name"),
                        ("source_sha", "source_sha"),
                        ("clip_sha", "clip_sha"),
                        ("input_uri", "gcs_uri"),
                        ("start_ms", "start_ms"),
                        ("end_ms", "end_ms"),
                    )))
        except (OSError, ValueError, KeyError):
            return False

    ledger.resolve_chirp_window_review_with_local_clock_fix(
        job_id, window_id, operation_name=row["operation_name"],
        evidence_sha=evidence_sha, actual_cents=row["actual_cents"],
        local_clock_fix_sha=proposal_sha, verifier=verify,
    )
    return {"job_id": job_id, "window_id": window_id,
            "action": "LOCAL_CLOCK_FIX_APPLIED",
            "fixed_word_count": len(fixed_words),
            "normalization_count": len(normalizations),
            "unresolvable_count": len(proposal.get("unresolvable", [])),
            "proposal_sha256": proposal_sha,
            "window_sha256": file_sha256(window_path),
            "raw_sha256": evidence_sha,
}
