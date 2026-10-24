"""Approval-bound Chirp 3 stage, with recoverable asynchronous operation IDs.

No calls occur on import. Submission requires an approved stage claim; each
request crosses a committed SUBMITTING barrier before provider network IO.
Recovery uses persisted operation IDs only. V1 production is unchanged.
"""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_audio_staging import file_sha256, prepare_local_clips
from app.providers.golden_v2_gcs_staging import upload_approved_clips
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.providers.golden_v2_versioned_artifacts import resolve_versioned, write_versioned


def _wire(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _immutable(path: Path, content: bytes) -> str:
    """Preserve exact raw provider bytes (including JSON formatting)."""
    return store_immutable_bytes(path, content)


def plan_approved_chirp(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    normalized_audio: Path, job_dir: Path,
    storage_client: Any, bucket_name: str, object_prefix: str,
    estimate_window_cents: Callable[[dict[str, Any]], int],
    max_window_seconds: float = 900.0, overlap_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Whole-source independent Chirp plan; no Gemini transcript is an input.

    All per-window estimates must fit the preapproved stage budget before any
    GCS upload. GCS blobs are source/clip SHA bound and cannot be overwritten.
    """
    ledger.assert_stage_claim(claim, stage="chirp_independent")
    if not callable(estimate_window_cents) or not 0 < max_window_seconds <= 1200:
        raise V2Conflict("Chirp cost estimator and <=20-minute windows required")
    manifest_path = job_dir / "golden-v2" / "source-lineage.json"
    try:
        lineage = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V2Conflict("missing verified Gemini-first original-source lineage") from exc
    if (lineage.get("schema") != "golden-v2-source-lineage-v1"
            or lineage.get("normalized_sha256") != claim["source_sha"]):
        raise V2Conflict("Chirp source differs from original Drive/normalized provenance")
    local = prepare_local_clips(
        source=normalized_audio, expected_source_sha256=claim["source_sha"],
        output_dir=job_dir / "golden-v2" / "chirp-clips",
        max_window_seconds=max_window_seconds, overlap_seconds=overlap_seconds,
    )
    estimates = [estimate_window_cents(row) for row in local["windows"]]
    if (any(type(cost) is not int or cost < 0 for cost in estimates)
            or sum(estimates) > claim["estimate_cents"]):
        raise V2Conflict("planned Chirp requests exceed approved stage cost")
    staged = upload_approved_clips(
        ledger=ledger, claim=claim, local_manifest=local,
        local_dir=job_dir / "golden-v2" / "chirp-clips",
        bucket_name=bucket_name, object_prefix=object_prefix,
        storage_client=storage_client,
    )
    planned = []
    for window, cost in zip(staged, estimates):
        idx = int(window["index"])
        clip_sha = window["clip_sha256"]
        # Outputs are unique to one job and source; the provider must write
        # strictly inside this private result prefix.
        output = (f"gs://{bucket_name}/{object_prefix}/{claim['job_id']}/"
                  f"{claim['source_sha']}/chirp-outputs/{idx:04d}-{clip_sha[:24]}/")
        bound = {
            "window_id": f"chunk-{idx:04d}", "source_sha": claim["source_sha"],
            "clip_sha": clip_sha, "gcs_uri": window["gcs_uri"],
            "start_ms": round(window["start_seconds"] * 1000),
            "end_ms": round(window["end_seconds"] * 1000),
            "output_uri": output, "estimated_cents": cost,
        }
        ledger.plan_chirp_window(claim, bound)
        planned.append(bound)
    return planned


def submit_planned_chirp(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any], gateway: Any,
) -> list[dict[str, Any]]:
    """Submit once; ambiguous network outcomes remain SUBMITTING until review.

    Gateway implements submit(window) -> provider operation name. The gateway
    must never retry its POST automatically; SDK retry must be disabled.
    """
    ledger.assert_stage_claim(claim, stage="chirp_independent")
    rows = ledger.chirp_windows(claim["job_id"])
    if not rows:
        raise V2Conflict("Chirp input not staged/approved")
    for row in rows:
        if row["status"] == "PLANNED":
            if (row.get("purpose") == "REPAIR"
                    and getattr(gateway, "dynamic_batching", None) is not False):
                raise V2Conflict("Standard repair requires dynamic batching to be disabled")
            submission = ledger.begin_chirp_submit(claim, row["window_id"])
            # No exception path may revert SUBMITTING to PLANNED, even when a
            # timeout conceals a successfully submitted paid request.
            name = gateway.submit(submission)
            ledger.record_chirp_submission(claim, row["window_id"], name)
        elif row["status"] not in {"SUBMITTED", "DONE"}:
            raise V2Conflict("unresolved Chirp operation; no automatic resubmission")
    return ledger.chirp_windows(claim["job_id"])


_DURATION = re.compile(r"^(\d+)(?:\.(\d{1,9}))?s$")


def _offset_ms(value: Any) -> int:
    if not isinstance(value, str) or not (match := _DURATION.fullmatch(value)):
        raise V2Conflict("invalid Speech word time offset")
    return int(match.group(1)) * 1000 + round(int((match.group(2) or "").ljust(9, "0")) / 1_000_000)


def _parse_words(raw_bytes: bytes, window: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate actual Speech V2 BatchRecognizeResults JSON (no fake timing)."""
    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, ValueError) as exc:
        raise V2Conflict("provider result JSON invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise V2Conflict("Speech V2 result list missing")
    words: list[dict[str, Any]] = []
    for result_index, result in enumerate(payload["results"]):
        alts = result.get("alternatives") if isinstance(result, dict) else None
        if not isinstance(alts, list):
            raise V2Conflict("Speech recognition alternatives missing")
        if not alts:
            continue
        entries = alts[0].get("words") if isinstance(alts[0], dict) else None
        if not isinstance(entries, list):
            raise V2Conflict("Speech word timing evidence missing")
        entry_index = 0
        while entry_index < len(entries):
            entry = entries[entry_index]
            if not isinstance(entry, dict) or not isinstance(entry.get("word"), str) or not entry["word"]:
                raise V2Conflict("Speech word is invalid")
            # Speech protobuf JSON omits the zero-valued starting Duration.
            # Permit that default only for the first timed word in a chunk;
            # missing subsequent offsets remain invalid evidence.
            start = _offset_ms(entry.get("startOffset", "0s" if not words else "")) + window["start_ms"]
            word_text = entry["word"]
            resolution = None
            if "endOffset" not in entry:
                # Speech V2 JSON can omit default-zero word ends. Preserve the
                # text and use a same-start successor's real end as one grouped
                # timing span; never invent a boundary for an individual word.
                successor = entry_index + 1
                while successor < len(entries):
                    candidate = entries[successor]
                    if (not isinstance(candidate, dict)
                            or not isinstance(candidate.get("word"), str)
                            or not candidate["word"]):
                        raise V2Conflict("Speech word is invalid")
                    candidate_start = _offset_ms(
                        candidate.get("startOffset", "0s" if not words else "")
                    ) + window["start_ms"]
                    if candidate_start != start:
                        raise V2Conflict("missing word end has no same-time successor")
                    word_text += candidate["word"]
                    if "endOffset" in candidate:
                        end = _offset_ms(candidate["endOffset"]) + window["start_ms"]
                        resolution = "COALESCED_ZERO_DURATION_SEQUENCE"
                        entry_index = successor
                        break
                    successor += 1
                else:
                    raise V2Conflict("missing word end has no same-time successor")
            else:
                end = _offset_ms(entry["endOffset"]) + window["start_ms"]
            if (end <= start or start < window["start_ms"]
                    or end > window["end_ms"] + 250):
                raise V2Conflict("Speech word offsets out of source window")
            word = {"word": word_text, "start_ms": start, "end_ms": end}
            if resolution:
                word["timing_resolution"] = resolution
            words.append(word)
            entry_index += 1
    # Provider arrays may contain one late-arriving word after later words.
    # Absolute offsets remain authoritative; sort, then fail closed on overlap.
    words.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    for earlier, later in zip(words, words[1:]):
        if later["start_ms"] < earlier["end_ms"]:
            raise V2Conflict("Speech word offsets overlap in source window")
    return words


def recover_chirp_window(
    *, ledger: GoldenV2Ledger, job_id: str, window_id: str,
    job_dir: Path, gateway: Any, cost_accountant: Callable[[dict[str, Any]], int],
) -> str:
    """Read only a persisted submitted operation; PENDING never submits."""
    windows = {row["window_id"]: row for row in ledger.chirp_windows(job_id)}
    row = windows.get(window_id)
    if row is None or row["status"] not in {"SUBMITTED", "DONE"} or not row["operation_name"]:
        raise V2Conflict("cannot recover missing/unknown Chirp operation ID")
    if row["status"] == "DONE":
        return "DONE"
    result = gateway.read_result(row)
    if result.get("status") == "PENDING":
        return "PENDING"
    if result.get("status") != "COMPLETE" or result.get("operation_name") != row["operation_name"]:
        raise V2Conflict("provider operation failed, was ambiguous or mismatched")
    raw = result.get("raw_json")
    if not isinstance(raw, (str, bytes)):
        raise V2Conflict("no provider raw JSON to recover")
    content = raw.encode("utf-8") if isinstance(raw, str) else raw
    root = job_dir / "golden-v2" / "chirp-evidence" / window_id
    evidence_sha = _immutable(root / "provider-raw.json", content)
    actual_cents = cost_accountant(row)
    if type(actual_cents) is not int or actual_cents < 0:
        raise V2Conflict("verified Chirp cost reconciliation is required")
    try:
        words = _parse_words(content, row)
        if not words:
            # Silence can be valid, but only an independent VAD may approve it.
            raise V2Conflict("empty Chirp result requires independent acoustic/VAD review")
    except V2Conflict as exc:
        # A provider-complete response with unusable timing is a durable review
        # result, not a worker crash and never a successful transcript window.
        review = {
            "schema": "golden-v2-chirp-window-review-v1", "job_id": job_id,
            "operation_name": row["operation_name"], "request_id": row["request_id"],
            "window_id": window_id, "source_sha": row["source_sha"],
            "clip_sha": row["clip_sha"], "raw_sha": evidence_sha,
            "actual_cents": actual_cents, "reason": str(exc),
            "status": "REVIEW_REQUIRED",
        }
        review_path = root / "review-required.json"
        _immutable(review_path, _wire(review))

        def verify_review(**expected: Any) -> bool:
            try:
                saved = json.loads(review_path.read_bytes())
                return (file_sha256(root / "provider-raw.json") == expected["evidence_sha"]
                        and saved == review
                        and all(saved[key] == expected[value] for key, value in (
                            ("operation_name", "operation_name"),
                            ("source_sha", "source_sha"),
                            ("clip_sha", "clip_sha"),
                        )))
            except (OSError, ValueError, KeyError):
                return False

        ledger.record_chirp_review_required(
            job_id, window_id, operation_name=row["operation_name"],
            evidence_sha=evidence_sha, actual_cents=actual_cents,
            verifier=verify_review,
        )
        return "REVIEW_REQUIRED"
    manifest = {
        "schema": "golden-v2-chirp-window-v1", "job_id": job_id,
        "operation_name": row["operation_name"], "request_id": row["request_id"],
        "window_id": window_id, "source_sha": row["source_sha"],
        "clip_sha": row["clip_sha"], "input_uri": row["gcs_uri"],
        "output_uri": row["output_uri"], "start_ms": row["start_ms"],
        "end_ms": row["end_ms"], "raw_sha": evidence_sha,
        "words": words, "actual_cents": actual_cents,
        "acoustic_gate": "NOT_RUN",
    }
    _immutable(root / "window.json", _wire(manifest))

    def verify(**expected: Any) -> bool:
        try:
            saved = json.loads((root / "window.json").read_bytes())
            return (file_sha256(root / "provider-raw.json") == expected["evidence_sha"]
                    and saved == manifest
                    and all(saved[key] == expected[value] for key, value in (
                        ("operation_name", "operation_name"), ("source_sha", "source_sha"),
                        ("clip_sha", "clip_sha"), ("input_uri", "gcs_uri"),
                        ("start_ms", "start_ms"), ("end_ms", "end_ms"),
                    )))
        except (OSError, ValueError, KeyError):
            return False

    ledger.recover_submitted_chirp(
        job_id, window_id, operation_name=row["operation_name"],
        evidence_sha=evidence_sha, actual_cents=actual_cents, verifier=verify,
    )
    return "DONE"


def reconcile_reviewed_chirp_window(
    *, ledger: GoldenV2Ledger, job_id: str, window_id: str, job_dir: Path,
) -> str:
    """Re-parse quarantined immutable raw output after a bounded parser fix.

    This path performs no provider request and never changes the accounted
    charge. Results with unresolved overlaps or missing timing stay held.
    """
    rows = {row["window_id"]: row for row in ledger.chirp_windows(job_id)}
    row = rows.get(window_id)
    if row is None or row["status"] != "REVIEW_REQUIRED" or not row["operation_name"]:
        raise V2Conflict("no quarantined Chirp operation to reconcile")
    if type(row["actual_cents"]) is not int or row["actual_cents"] < 0:
        raise V2Conflict("quarantined Chirp charge is unresolved")
    root = job_dir / "golden-v2" / "chirp-evidence" / window_id
    raw_path = root / "provider-raw.json"
    review_path = root / "review-required.json"
    try:
        raw_bytes = raw_path.read_bytes()
        review = json.loads(review_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise V2Conflict("quarantined raw or review evidence is missing") from exc
    evidence_sha = file_sha256(raw_path)
    if (evidence_sha != row["result_sha"]
            or review.get("schema") != "golden-v2-chirp-window-review-v1"
            or review.get("job_id") != job_id or review.get("window_id") != window_id
            or review.get("operation_name") != row["operation_name"]
            or review.get("raw_sha") != evidence_sha
            or review.get("actual_cents") != row["actual_cents"]):
        raise V2Conflict("quarantined raw identity or cost evidence changed")
    repaired_evidence = None
    try:
        words = _parse_words(raw_bytes, row)
    except V2Conflict as exc:
        # Only a known offset/overlap defect may enter the provider-free,
        # lossless window normalizer. This fixes words' timestamp geometry;
        # it does NOT certify that the repaired timing matches the audio.
        # Inexact provider grammar, absent words or unknown errors stay held.
        # The provider may return an otherwise valid Standard repair chunk
        # with an omitted final word end and no same-time successor. A
        # source-anchored, text-lossless timestamp fill is a draft-only
        # recovery, never evidence that an acoustic gap has been resolved.
        known_timing_defect = str(exc) in {
            "Speech word offsets out of source window",
            "Speech word offsets overlap in source window",
        } or (
            row.get("purpose") == "REPAIR"
            and str(exc) == "missing word end has no same-time successor"
        )
        if not known_timing_defect:
            return "REVIEW_REQUIRED"
        from app.pipeline.golden_v2_chirp_window_repair import (
            _offset_ms as safe_offset_ms, repair_window,
        )
        try:
            payload = json.loads(raw_bytes)
            entries = [entry for result in payload.get("results", [])
                       for alt in result.get("alternatives", [])[:1]
                       for entry in alt.get("words", [])]
            # At least one original provider word must anchor the repaired
            # timeline; clock-only guesses without any source timestamp are
            # not eligible for local reconciliation.
            if not any(
                safe_offset_ms(entry.get("startOffset")) is not None
                and safe_offset_ms(entry.get("endOffset")) is not None
                and 0 <= safe_offset_ms(entry["startOffset"])
                < safe_offset_ms(entry["endOffset"]) <= row["end_ms"] - row["start_ms"] + 250
                for entry in entries
            ):
                return "REVIEW_REQUIRED"
            repair = repair_window(raw_bytes, row)
            if (repair.characters_lost != 0 or not repair.words
                    or any(
                        type(w.get("start_ms")) is not int
                        or type(w.get("end_ms")) is not int
                        or not row["start_ms"] <= w["start_ms"] < w["end_ms"] <= row["end_ms"] + 250
                        for w in repair.words
                    ) or any(left["end_ms"] > right["start_ms"]
                             for left, right in zip(repair.words, repair.words[1:]))):
                return "REVIEW_REQUIRED"
        except (V2Conflict, ValueError, TypeError, KeyError):
            return "REVIEW_REQUIRED"
        repaired_evidence = {
            "schema": "golden-v2-chirp-window-lossless-normalization-v1",
            "method": "REPARSE_QUARANTINED_RAW_WITH_LOSSLESS_WINDOW_REPAIR",
            "original_reason": str(exc),
            "provider_raw_sha256": evidence_sha,
            "original_characters": repair.source_characters,
            "repaired_characters": repair.repaired_characters,
            "characters_lost": repair.characters_lost,
            "acoustic_timing_verified": False,
            "summary": repair.summary(),
        }
        # Source-bound deterministic evidence is immutable and explicit.
        # The normalizer may shift clocks, but never invent/lose word text.
        # Full acoustic QA at finish_approved_chirp is still mandatory.
        words = repair.words
    if not words:
        return "REVIEW_REQUIRED"
    normalizations = [
        {"word": item["word"], "start_ms": item["start_ms"],
         "end_ms": item["end_ms"], "method": item["timing_resolution"]}
        for item in words if item.get("timing_resolution")
    ]
    manifest = {
        "schema": "golden-v2-chirp-window-v1", "job_id": job_id,
        "operation_name": row["operation_name"], "request_id": row["request_id"],
        "window_id": window_id, "source_sha": row["source_sha"],
        "clip_sha": row["clip_sha"], "input_uri": row["gcs_uri"],
        "output_uri": row["output_uri"], "start_ms": row["start_ms"],
        "end_ms": row["end_ms"], "raw_sha": evidence_sha,
        "words": words, "timing_normalizations": normalizations,
        "actual_cents": row["actual_cents"], "acoustic_gate": "NOT_RUN",
    }
    if repaired_evidence is not None:
        manifest["lossless_repair"] = repaired_evidence
        manifest["timing_normalizations"] = repaired_evidence["summary"]["repairs"]
    window_path = root / "window.json"
    _immutable(window_path, _wire(manifest))

    def verify(**expected: Any) -> bool:
        try:
            saved = json.loads(window_path.read_bytes())
            saved_review = json.loads(review_path.read_bytes())
            return (file_sha256(raw_path) == expected["evidence_sha"]
                    and saved == manifest
                    and saved_review == review
                    and all(saved[key] == expected[value] for key, value in (
                        ("operation_name", "operation_name"), ("source_sha", "source_sha"),
                        ("clip_sha", "clip_sha"), ("input_uri", "gcs_uri"),
                        ("start_ms", "start_ms"), ("end_ms", "end_ms"),
                    )))
        except (OSError, ValueError, KeyError):
            return False

    ledger.resolve_chirp_window_review(
        job_id, window_id, operation_name=row["operation_name"],
        evidence_sha=evidence_sha, actual_cents=row["actual_cents"],
        verifier=verify,
    )
    return "DONE"


def finish_approved_chirp(
    *, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
    acoustic_inspector: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge base+repair evidence; complete only after local acoustic PASS.

    A REVIEW result preserves every immutable attempt and returns an explicit
    bounded Standard repair proposal or a manual-review disposition. It never
    submits a provider request.
    """
    windows = ledger.chirp_windows(job_id)
    if not windows or any(w["status"] != "DONE" for w in windows):
        raise V2Conflict("all Chirp operations must have verified results")
    base = [w for w in windows if w.get("purpose", "BASE") == "BASE"]
    repairs = [w for w in windows if w.get("purpose") == "REPAIR"]
    if not base:
        raise V2Conflict("no original full-coverage Chirp windows")
    source = job_dir / "normalized.flac"
    if not source.is_file() or file_sha256(source) != base[0]["source_sha"]:
        raise V2Conflict("normalized audio missing or changed before Chirp merge")
    # Windows must cover the entire normalized source. FFprobe duration is
    # checked independently; no unrecorded time span can be skipped.
    from app.providers.golden_v2_audio_staging import duration_seconds
    duration_ms = round(duration_seconds(source) * 1000)
    if base[0]["start_ms"] != 0 or abs(base[-1]["end_ms"] - duration_ms) > 250:
        raise V2Conflict("Chirp plan does not cover original normalized audio")
    for earlier, later in zip(base, base[1:]):
        if later["start_ms"] > earlier["end_ms"]:
            raise V2Conflict("missing Chirp window region")
    # Bind this round to the prior completed, ledger-attested merge. Rebuild
    # anchored base windows from raw below, or retain the prior baseline when
    # historical raw cannot support a new timing hypothesis.
    prior_sha = ledger.prior_chirp_merge_for_active_repair(job_id)
    prior_results: set[str] = set()
    prior: dict[str, Any] = {}
    # The immutable provider raw is the source of truth. Recompute every base
    # window losslessly from it and keep the result only when NOT ONE
    # recognized character is lost. A stored window.json is a derived cache and
    # may have been written by an older, lossy parser.
    from app.pipeline.golden_v2_chirp_window_repair import (
        _offset_ms as safe_offset_ms, repair_window,
    )
    base_lossless: dict[str, list[dict[str, Any]]] = {}
    base_repair_evidence: dict[str, dict[str, Any]] = {}
    for row in base:
        raw_path = job_dir / "golden-v2" / "chirp-evidence" / row["window_id"] / "provider-raw.json"
        try:
            raw_bytes = raw_path.read_bytes()
            payload = json.loads(raw_bytes)
            # A raw response with no complete, in-window timing anchor cannot
            # replace an already-attested baseline using DEFAULT_TAIL_FILL.
            # Preserve that baseline; a new timing hypothesis needs review.
            entries = [entry for result in payload.get("results", [])
                       for alt in result.get("alternatives", [])[:1]
                       for entry in alt.get("words", [])]
            if not any(
                safe_offset_ms(entry.get("startOffset")) is not None
                and safe_offset_ms(entry.get("endOffset")) is not None
                and 0 <= safe_offset_ms(entry["startOffset"])
                < safe_offset_ms(entry["endOffset"])
                <= row["end_ms"] - row["start_ms"] + 250
                for entry in entries
            ):
                continue
            repaired = repair_window(raw_bytes, row)
        except (OSError, ValueError, V2Conflict):
            continue
        base_lossless[row["window_id"]] = repaired.words
        base_repair_evidence[row["window_id"]] = {
            "raw_sha": row["result_sha"], "summary": repaired.summary(),
            "acoustic_timing_verified": False,
        }
    has_lossless_base_repair = len(base_lossless) == len(base) and bool(base)
    if prior_sha is not None:
        prior_path = resolve_versioned(job_dir / "golden-v2" / "chirp-merged.json",
                                       prior_sha)
        prior = json.loads(prior_path.read_bytes())
        previous = prior.get("window_result_shas")
        known = [row["result_sha"] for row in windows]
        if (prior.get("schema") != "golden-v2-chirp-merged-v1"
                or prior.get("source_sha") != base[0]["source_sha"]
                or prior.get("job_id") != job_id
                or prior.get("duration_ms") != duration_ms
                or prior.get("timing_authority") != "chirp_3"
                or not isinstance(prior.get("words"), list)
                or not isinstance(previous, list)
                or not previous
                or not isinstance(prior.get("actual_cents"), int)
                or any(previous.count(item) > known.count(item) for item in set(previous))
                or [w["result_sha"] for w in base] != previous[:len(base)]):
            raise V2Conflict("previous completed Chirp merge is not bound to raw operations")
        prior_results = set(previous)
        if prior["actual_cents"] != sum(
                int(row["actual_cents"]) for row in windows
                if row["result_sha"] in prior_results):
            raise V2Conflict("previous completed Chirp merge cost changed")
    if prior_sha is not None and not has_lossless_base_repair:
        merged: list[dict[str, Any]] = [dict(word) for word in prior["words"]]
    else:
        merged = []

    records: dict[str, list[dict[str, Any]]] = {}
    for row in windows:
        root = job_dir / "golden-v2" / "chirp-evidence" / row["window_id"]
        saved = json.loads((root / "window.json").read_bytes())
        if (saved["raw_sha"] != row["result_sha"]
                or saved["actual_cents"] != row["actual_cents"]
                or saved["request_id"] != row["request_id"]
                or file_sha256(root / "provider-raw.json") != row["result_sha"]):
            raise V2Conflict("Chirp raw evidence or cost changed after recovery")
        if row["window_id"] in base_lossless:
            records[row["window_id"]] = base_lossless[row["window_id"]]
        elif row["result_sha"] not in prior_results or (
                has_lossless_base_repair and row.get("purpose") == "REPAIR"):
            # A rebuilt base does not contain ANY historical repair. Reparse
            # every completed repair so replay cannot silently drop paid text.
            raw_bytes = (root / "provider-raw.json").read_bytes()
            try:
                records[row["window_id"]] = _parse_words(raw_bytes, row)
            except V2Conflict:
                if row.get("purpose") == "REPAIR" or has_lossless_base_repair:
                    records[row["window_id"]] = repair_window(raw_bytes, row).words
                else:
                    raise
    merge_timing_evidence: list[dict[str, Any]] = []
    if prior_sha is None or has_lossless_base_repair:
        from app.pipeline.golden_v2_chirp_window_repair import merge_base_windows
        merged = merge_base_windows(
            [{"start_ms": row["start_ms"], "end_ms": row["end_ms"],
              "words": records[row["window_id"]]} for row in base],
            duration_ms, audit=merge_timing_evidence,
        )
    else:
        merge_timing_evidence = prior.get("merge_timing_evidence", [])
    for row in repairs:
        if row["result_sha"] in prior_results and not has_lossless_base_repair:
            continue
        try:
            targets = json.loads(row["target_intervals_json"])
        except (TypeError, ValueError) as exc:
            raise V2Conflict("persisted repair target evidence is invalid") from exc
        if not targets:
            raise V2Conflict("repair operation has no approved target interval")
        def targeted(word: dict[str, Any]) -> bool:
            midpoint = (word["start_ms"] + word["end_ms"]) / 2
            return any(target["start_ms"] <= midpoint < target["end_ms"] for target in targets)
        kept_base: list[dict[str, Any]] = []
        for word in merged:
            if targeted(word):
                continue
            wcopy = dict(word)
            for target in targets:
                if wcopy["start_ms"] < target["start_ms"] < wcopy["end_ms"]:
                    wcopy["end_ms"] = target["start_ms"]
                elif wcopy["start_ms"] < target["end_ms"] < wcopy["end_ms"]:
                    wcopy["start_ms"] = target["end_ms"]
            if wcopy["end_ms"] > wcopy["start_ms"]:
                kept_base.append(wcopy)
        merged = kept_base
        for word in records[row["window_id"]]:
            if not targeted(word):
                continue
            wcopy = dict(word)
            midpoint = (wcopy["start_ms"] + wcopy["end_ms"]) / 2
            for target in targets:
                if target["start_ms"] <= midpoint < target["end_ms"]:
                    wcopy["start_ms"] = max(wcopy["start_ms"], target["start_ms"])
                    wcopy["end_ms"] = min(wcopy["end_ms"], target["end_ms"])
                    break
            if wcopy["end_ms"] > wcopy["start_ms"]:
                merged.append(wcopy)
    merged.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    previous_end = 0
    for word in merged:
        if word["start_ms"] < previous_end:
            raise V2Conflict("merged base/repair Chirp words overlap")
        previous_end = word["end_ms"]
    if not merged:
        raise V2Conflict("empty merged Chirp words require acoustic review")
    candidate = {
        "schema": "golden-v2-chirp-merged-v1", "job_id": job_id,
        "source_sha": base[0]["source_sha"], "duration_ms": duration_ms,
        "window_result_shas": [w["result_sha"] for w in windows],
        "repair_result_shas": [w["result_sha"] for w in repairs],
        "actual_cents": sum(int(w["actual_cents"]) for w in windows),
        "words": merged, "timing_authority": "chirp_3",
        # Provider ancestry is not proof that every normalized boundary was
        # acoustically verified. Retain the actual normalization decisions.
        "timing_normalization_evidence": (
            base_repair_evidence if has_lossless_base_repair else
            prior.get("timing_normalization_evidence", {})),
        "merge_timing_evidence": merge_timing_evidence,
        "acoustic_timing_verified": False,
        "acoustic_gate": "NOT_RUN", "golden_pass": False,
    }
    candidate_sha = hashlib.sha256(_wire(candidate)).hexdigest()
    candidate_path = (job_dir / "golden-v2" /
                      f"chirp-merged-candidate-{candidate_sha[:20]}.json")
    _immutable(candidate_path, _wire(candidate))
    if acoustic_inspector is None:
        from app.providers.golden_v2_acoustic_qa import inspect_v2_acoustic
        acoustic_inspector = inspect_v2_acoustic
    acoustic = acoustic_inspector(job_dir=job_dir, merged=candidate)
    if (not isinstance(acoustic, dict) or acoustic.get("status") not in {"PASS", "REVIEW"}
            or acoustic.get("source_sha256") != candidate["source_sha"]
            or acoustic.get("merged_sha256") != candidate_sha):
        raise V2Conflict("independent acoustic QA did not bind to this Chirp merge")
    acoustic_bytes = _wire(acoustic)
    acoustic_sha = hashlib.sha256(acoustic_bytes).hexdigest()
    acoustic_path = (job_dir / "golden-v2" /
                     f"chirp-acoustic-qa-{acoustic_sha[:20]}.json")
    _immutable(acoustic_path, acoustic_bytes)
    if acoustic["status"] != "PASS":
        result = dict(candidate)
        result.update({
            "acoustic_gate": "REVIEW", "acoustic_qa_sha256": acoustic_sha,
            "acoustic_qa_path": str(acoustic_path),
            "candidate_path": str(candidate_path),
            "repair_plan": acoustic.get("repair_plan", {}),
            "manual_review_required": bool(
                acoustic.get("repair_plan", {}).get("manual_review_required", True)
            ),
        })
        return result

    report = dict(candidate)
    report.update({"acoustic_gate": "PASS", "acoustic_qa_sha256": acoustic_sha})
    dest = job_dir / "golden-v2" / "chirp-merged.json"
    digest, selected = write_versioned(dest, _wire(report))

    def verify(**expected: Any) -> bool:
        return (expected["job_id"] == job_id and expected["source_sha"] == report["source_sha"]
                and expected["evidence_sha"] == file_sha256(selected)
                and len(expected["windows"]) == len(windows)
                and all(item["result_sha"] == saved_sha for item, saved_sha in
                        zip(expected["windows"], report["window_result_shas"])))

    ledger.finish_recovered_chirp(job_id, evidence_sha=digest, verifier=verify)
    return report
