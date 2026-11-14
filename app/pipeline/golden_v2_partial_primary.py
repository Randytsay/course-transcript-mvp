"""Partial Gemini primary evidence for compare after a verified SAFETY refusal.

When a Gemini primary is held at REVIEW_REQUIRED because one or more windows
were refused (empty text + SAFETY), the complete windows are still comparable
evidence. This module derives an immutable, SHA-bound partial primary that
carries only the STOP windows' text, marks the refused spans as
Chirp-authoritative, and scopes coverage to the comparable span. It never
modifies the original primary-stage.json, never completes Gemini, and never
claims acoustic coverage from text differences.

Partial mode opens only after an independent-rescue claim is recorded in the
ledger (reviewer 'trusted-operator-independent-rescue') and
verify_primary_refusal passes again against the persisted evidence. The
refused spans are reviewed as bounded Chirp-authoritative pieces (at most
120 seconds each, the adjudication window bound), never auto-resolved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_primary_rescue import verify_primary_refusal
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_compare import _letters
from app.providers.golden_v2_immutable import store_immutable_bytes

PARTIAL_PRIMARY_SCHEMA = "golden-v2-primary-stage-partial-v1"
PARTIAL_PRIMARY_FILENAME = "primary-stage-partial.json"
RESCUE_REVIEWER = "trusted-operator-independent-rescue"
# Adjudication resolves a candidate only against a reviewed audio window of
# at most 120 seconds, so no refused-span piece may be longer than that.
MAX_REVIEW_PIECE_MS = 120_000


def _wire(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _window_ms(item: dict[str, Any]) -> tuple[int, int]:
    window = (item.get("identity") or {}).get("window") or {}
    start, end = window.get("start_seconds"), window.get("end_seconds")
    if (not isinstance(start, (int, float))
            or not isinstance(end, (int, float)) or end <= start):
        raise V2Conflict("partial primary window has no valid time span")
    return round(float(start) * 1000), round(float(end) * 1000)


def _union_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_spans(total: list[tuple[int, int]],
                    refused: list[tuple[int, int]]) -> list[tuple[int, int]]:
    scope: list[tuple[int, int]] = []
    for start, end in total:
        cursor = start
        for rstart, rend in refused:
            if rend <= cursor or rstart >= end:
                continue
            if rstart > cursor:
                scope.append((cursor, min(rstart, end)))
            cursor = max(cursor, rend)
            if cursor >= end:
                break
        if cursor < end:
            scope.append((cursor, end))
    return scope


def _span_ms(spans: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in spans)


def _resolve_window_records(*, job_dir: Path, report: dict[str, Any],
                            ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Resolve each window's raw audit by checksum, as verify_primary_refusal does."""
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in report["window_evidence"]:
        identity = item["identity"]
        matches = [path for path in (Path(job_dir) / "gemini-primary").glob("*.json")
                   if not path.is_symlink()
                   and file_sha256(path) == item["raw_audit_sha256"]]
        if len(matches) != 1:
            raise V2Conflict("immutable raw audit missing or ambiguous")
        try:
            raw = json.loads(matches[0].read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise V2Conflict("immutable raw audit unreadable") from exc
        if raw.get("identity") != identity:
            raise V2Conflict("raw identity mismatch")
        records.append((item, raw))
    return records


def build_partial_primary(*, ledger: GoldenV2Ledger, job_id: str,
                          job_dir: Path) -> dict[str, Any]:
    """Derive (or reuse) the immutable partial primary for a held job.

    Idempotent: the same evidence always produces the same bytes, and
    store_immutable_bytes refuses a conflicting file rather than overwriting.
    """
    job_dir = Path(job_dir)
    state = ledger.status(job_id)
    stages = {row["name"]: row for row in state.get("stages", [])}
    primary = stages.get("gemini_primary")
    if primary is None or primary.get("state") != "REVIEW_REQUIRED":
        raise V2Conflict("partial primary requires a held Gemini stage")
    if ledger.independent_rescue_resumption(job_id) is None:
        raise V2Conflict("partial primary requires a recorded independent rescue")
    if primary.get("evidence_sha") is None or primary.get("actual_cents") is None:
        raise V2Conflict("partial primary requires complete held-primary evidence")
    verify_primary_refusal(
        job_dir=job_dir, job_id=job_id, source_sha=state["source_sha"],
        evidence_sha=primary["evidence_sha"],
        actual_cents=primary["actual_cents"],
    )
    report_path = job_dir / "golden-v2" / "primary-stage.json"
    if report_path.is_symlink():
        raise V2Conflict("immutable primary evidence is a symlink")
    try:
        original = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V2Conflict("original primary evidence missing or invalid") from exc
    if (not isinstance(original, dict) or original.get("job_id") != job_id
            or original.get("source_sha256") != state["source_sha"]
            or original.get("schema") != "golden-v2-primary-stage-v1"
            or file_sha256(report_path) != primary["evidence_sha"]):
        raise V2Conflict("original primary evidence binding mismatch")
    assembled = original.get("assembled") or {}
    text = assembled.get("text")
    if not isinstance(text, str) or not text.strip():
        raise V2Conflict("partial primary has no comparable Gemini text")
    records = _resolve_window_records(job_dir=job_dir, report=original)
    covered_indices: list[int] = []
    stop_windows: list[dict[str, Any]] = []
    refused_windows: list[dict[str, Any]] = []
    seam_reviews: list[dict[str, Any]] = []
    all_spans: list[tuple[int, int]] = []
    stop_spans: list[tuple[int, int]] = []
    refused_spans: list[tuple[int, int]] = []
    for index, (item, raw) in enumerate(records):
        span = _window_ms(item)
        all_spans.append(span)
        if index and span[0] < all_spans[index-1][1]:
            seam_reviews.append({
                "between_windows": [index - 1, index],
                "start_ms": span[0], "end_ms": all_spans[index-1][1],
                "acoustic_review_required": True,
            })
        if (raw.get("status") == "COMPLETE"
                and raw.get("finish_reason") in {"STOP", "FINISH_REASON_STOP"}
                and raw.get("text", "").strip()):
            covered_indices.append(index)
            stop_spans.append(span)
            # Preserve each immutable STOP window separately; Gemini has no
            # word-level timestamps to justify automatic seam trimming.
            stop_windows.append({
                "index": index,
                "start_seconds": span[0] / 1000.0,
                "end_seconds": span[1] / 1000.0,
                "text": raw["text"].strip(),
                "raw_audit_sha256": item["raw_audit_sha256"],
            })
        elif (raw.get("status") == "REQUIRES_REVIEW" and not raw.get("text")
              and raw.get("raw_response", {}).get("prompt_feedback", {}).get("block_reason") == "SAFETY"):
            block = raw["raw_response"]["prompt_feedback"]["block_reason"]
            refused_windows.append({
                "index": index,
                "start_seconds": (item["identity"]["window"]["start_seconds"]),
                "end_seconds": (item["identity"]["window"]["end_seconds"]),
                "block_reason": block,
            })
            refused_spans.append(span)
        else:
            raise V2Conflict("partial primary window is not a known STOP or SAFETY refusal")
    if not covered_indices or not refused_windows:
        raise V2Conflict("partial primary needs at least one covered and one refused window")
    total = _union_spans(all_spans)
    # If a STOP window overlaps a refused one, its verified text is still
    # comparable in that shared 10-second interval. Only the remainder is
    # Chirp-authoritative. Preserve both windows and explicitly review seam.
    scope = _union_spans(stop_spans)
    refused_only = _subtract_spans(_union_spans(refused_spans), scope)
    refused_only_windows = [
        {"start_seconds": start / 1000.0,
         "end_seconds": end / 1000.0,
         "block_reason": "SAFETY",
         "index": -1}
        for start, end in refused_only
    ]
    covered_ms = _span_ms(scope)
    scope_ms = covered_ms
    if scope_ms <= 0 or covered_ms < 0:
        raise V2Conflict("partial primary comparison scope is empty")
    partial = {
        "schema": PARTIAL_PRIMARY_SCHEMA,
        "job_id": job_id,
        "source_sha256": state["source_sha"],
        "original_primary_evidence_sha256": primary["evidence_sha"],
        "assembled_text": text,
        "covered_window_indices": covered_indices,
        "stop_windows": stop_windows,
        "refused_windows": refused_windows,
        "refused_only_windows": refused_only_windows,
        "unresolved_seams": seam_reviews,
        "window_evidence": original["window_evidence"],
        "total_span_seconds": _span_ms(total) / 1000.0,
        "refused_span_seconds": _span_ms(_union_spans(refused_spans)) / 1000.0,
        "refused_only_span_seconds": _span_ms(refused_only) / 1000.0,
        "comparison_scope_seconds": scope_ms / 1000.0,
        "covered_span_seconds": covered_ms / 1000.0,
    }
    digest = store_immutable_bytes(
        job_dir / "golden-v2" / PARTIAL_PRIMARY_FILENAME, _wire(partial))
    partial["evidence_sha256"] = digest
    return partial


def load_partial_primary(*, job_dir: Path, job_id: str, source_sha: str,
                         original_evidence_sha: str) -> dict[str, Any]:
    """Read and validate a persisted partial primary against its original."""
    path = Path(job_dir) / "golden-v2" / PARTIAL_PRIMARY_FILENAME
    if path.is_symlink():
        raise V2Conflict("immutable partial primary is a symlink")
    try:
        partial = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V2Conflict("partial primary missing or invalid") from exc
    if (not isinstance(partial, dict)
            or partial.get("schema") != PARTIAL_PRIMARY_SCHEMA
            or partial.get("job_id") != job_id
            or partial.get("source_sha256") != source_sha
            or partial.get("original_primary_evidence_sha256") != original_evidence_sha
            or not isinstance(partial.get("assembled_text"), str)
            or not partial["assembled_text"].strip()
            or not isinstance(partial.get("refused_windows"), list)
            or not partial["refused_windows"]
            or not isinstance(partial.get("stop_windows"), list)
            or not partial["stop_windows"]
            or not isinstance(partial.get("unresolved_seams"), list)
            or not isinstance(partial.get("refused_only_windows"), list)):
        raise V2Conflict("partial primary binding mismatch")
    return partial


def partial_primary_coverage(partial: dict[str, Any]) -> float | None:
    """Coverage of the comparison scope (never of the whole audio)."""
    scope = partial.get("comparison_scope_seconds")
    covered = partial.get("covered_span_seconds")
    if (not isinstance(scope, (int, float))
            or not isinstance(covered, (int, float)) or scope <= 0):
        return None
    return min(1.0, max(0.0, covered / scope))


def _word_evidence_id(source_sha256: str, index: int, word: dict[str, Any]) -> str:
    return hashlib.sha256((
        f"{source_sha256}|{index}|{word['word']}|"
        f"{word['start_ms']}|{word['end_ms']}"
    ).encode()).hexdigest()


def refused_span_pieces(
    *, words: list[dict[str, Any]], refused_windows: list[dict[str, Any]],
    source_sha256: str, max_piece_ms: int = MAX_REVIEW_PIECE_MS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split refused spans into bounded Chirp-authoritative review pieces.

    Returns (piece candidates, authoritative spans). A piece is a list of
    Chirp words inside one refused window, at most max_piece_ms long, so a
    human can resolve it against one bounded reviewed-audio window. Refused
    windows without Chirp words produce no piece; they stay listed in the
    refused windows and are covered by acoustic QA instead.
    """
    if type(max_piece_ms) is not int or max_piece_ms <= 0:
        raise V2Conflict("review piece bound must be positive")
    letter_counts = [len(_letters(str(word.get("word", "")))) for word in words]
    char_start: list[int] = []
    running = 0
    for count in letter_counts:
        char_start.append(running)
        running += count
    candidates: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    for window in sorted(refused_windows, key=lambda item: item["start_seconds"]):
        start_ms = round(float(window["start_seconds"]) * 1000)
        end_ms = round(float(window["end_seconds"]) * 1000)
        indices = [i for i, word in enumerate(words)
                   if start_ms <= int(word["start_ms"]) < end_ms]
        if not indices:
            # No Chirp word is not a certificate of silence. Preserve the
            # entire unknown interval as bounded review-only candidates.
            for piece_start in range(start_ms, end_ms, max_piece_ms):
                piece_end = min(end_ms, piece_start + max_piece_ms)
                candidates.append({
                    "kind": "REFUSED_SPAN_WITHOUT_CHIRP_WORDS",
                    "comparison_status": "REVIEW_REQUIRED",
                    "chirp_char_range": None,
                    "gemini_char_range": None,
                    "chirp_normalized_excerpt": "",
                    "gemini_normalized_excerpt": "",
                    "source_time_start": piece_start,
                    "source_time_end": piece_end,
                    "source_time_unit": "milliseconds",
                    "chirp_evidence_ids": [],
                    "chirp_timing_evidence": None,
                    "gemini_timing_evidence": None,
                    "auto_replacement_allowed": False,
                    "chirp_authoritative": False,
                    "gemini_crosscheck": "absent_safety_refusal",
                    "audio_review_required": True,
                })
            continue
        pieces: list[list[int]] = []
        current: list[int] = []
        piece_start = None
        for index in indices:
            word = words[index]
            if int(word["end_ms"]) - int(word["start_ms"]) > max_piece_ms:
                raise V2Conflict("Chirp word exceeds bounded review window")
            if (current and piece_start is not None
                    and int(word["end_ms"]) - piece_start > max_piece_ms):
                pieces.append(current)
                current = []
                piece_start = None
            if piece_start is None:
                piece_start = int(word["start_ms"])
            current.append(index)
        if current:
            pieces.append(current)
        for piece in pieces:
            first, last = words[piece[0]], words[piece[-1]]
            piece_start_ms, piece_end_ms = int(first["start_ms"]), int(last["end_ms"])
            excerpt = "".join(
                _letters(str(words[i].get("word", ""))) for i in piece)[:500]
            candidates.append({
                "kind": "CHIRP_AUTHORITATIVE_REFUSED_SPAN",
                "comparison_status": "REVIEW_REQUIRED",
                "chirp_char_range": [
                    char_start[piece[0]],
                    char_start[piece[-1]] + letter_counts[piece[-1]],
                ],
                "gemini_char_range": None,
                "chirp_normalized_excerpt": excerpt,
                "gemini_normalized_excerpt": "",
                "source_time_start": piece_start_ms,
                "source_time_end": piece_end_ms,
                "source_time_unit": "milliseconds",
                "chirp_evidence_ids": [
                    _word_evidence_id(source_sha256, i, words[i]) for i in piece
                ],
                "chirp_timing_evidence": {
                    "chirp_start_ms": piece_start_ms,
                    "chirp_end_ms": piece_end_ms,
                },
                "adjacent_chirp_context": {
                    "left_chirp_word_ms": (
                        words[piece[0] - 1]["end_ms"] if piece[0] > 0 else None),
                    "right_chirp_word_ms": (
                        words[piece[-1] + 1]["start_ms"]
                        if piece[-1] + 1 < len(words) else None),
                },
                "gemini_timing_evidence": None,
                "auto_replacement_allowed": False,
                "chirp_authoritative": True,
                "gemini_crosscheck": "absent_safety_refusal",
            })
            spans.append({
                "start_ms": piece_start_ms,
                "end_ms": piece_end_ms,
                "start_word_index": piece[0],
                "end_word_index": piece[-1],
                "gemini_crosscheck": "absent_safety_refusal",
                "chirp_authoritative": True,
            })
    return candidates, spans


def word_in_refused_span(word: dict[str, Any],
                         refused_windows: list[dict[str, Any]]) -> bool:
    """True when a Chirp word starts inside a refused window's time span."""
    start_ms = int(word["start_ms"])
    return any(
        round(float(window["start_seconds"]) * 1000) <= start_ms
        < round(float(window["end_seconds"]) * 1000)
        for window in refused_windows
    )
