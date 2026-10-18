"""Bounded Chirp-3 local clock-fix reuse recovery for Golden V2 pipeline.

Grounding & Invariants:
1. Reuses existing provider results from provider-raw.json without new provider network calls.
2. provider-raw.json is immutable and its SHA-256 is strictly preserved.
3. Billed actual_cents is preserved from provider receipt (5 cents per window).
4. Strictly monotonic word boundaries with zero overlap.
5. Rejection if >50% of entries require repair.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict

_OFFSET_RE = re.compile(r"^(\d+)(?:\.(\d{1,9}))?s$")


def _offset_ms(value: Any) -> int:
    if not isinstance(value, str) or not (m := _OFFSET_RE.fullmatch(value)):
        raise V2Conflict(f"invalid Speech word time offset: {value!r}")
    return int(m.group(1)) * 1000 + round(int((m.group(2) or "").ljust(9, "0")) / 1_000_000)


def _wire(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclasses.dataclass
class FixDecision:
    index: int
    method: str
    word: str
    original_start: Any
    original_end: Any
    fixed_start_ms: int
    fixed_end_ms: int


def _normalize_window(
    entries: list[dict[str, Any]], win_start_ms: int, win_end_ms: int, window_id: str
) -> tuple[list[dict[str, Any]], list[FixDecision], list[dict[str, Any]]]:
    fixes: list[FixDecision] = []
    unresolvable: list[dict[str, Any]] = []
    repaired_entries: list[dict[str, Any]] = []
    current_slip_ms = 0
    n = len(entries)
    would_repair = 0

    # Step 1: Detect and repair intra-window clock slips
    # (e.g. Speech API clock wrapping backwards by ~100s as observed in chunk-0009)
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("word"), str) or not entry["word"]:
            unresolvable.append({"index": i, "reason": "invalid word record", "entry": entry})
            would_repair += 1
            continue

        start_off = entry.get("startOffset")
        end_off = entry.get("endOffset")
        has_start = isinstance(start_off, str) and bool(_OFFSET_RE.fullmatch(start_off or ""))
        has_end = isinstance(end_off, str) and bool(_OFFSET_RE.fullmatch(end_off or ""))

        s_ms = _offset_ms(start_off) if has_start else None
        e_ms = _offset_ms(end_off) if has_end else None

        # Check for slip onset: e.g. startOffset was 763s, but endOffset jumped back by >10s (e.g. 664s)
        if s_ms is not None and e_ms is not None and e_ms < s_ms - 10000:
            slip_amount = s_ms - e_ms
            current_slip_ms += slip_amount
            repaired_entries.append({
                "entry": entry, "_s_ms": s_ms, "_e_ms": e_ms + current_slip_ms,
                "_orig_index": i, "word": entry["word"], "start_off": start_off, "end_off": end_off,
            })
            continue

        if current_slip_ms > 0:
            s_ms_adj = s_ms + current_slip_ms if s_ms is not None else None
            if e_ms is not None:
                # Check if clock recovered forward (endOffset jumps forward by >50s relative to s_ms)
                if s_ms is not None and e_ms - s_ms > 50000:
                    current_slip_ms = 0
                    e_ms_adj = e_ms
                else:
                    e_ms_adj = e_ms + current_slip_ms
            else:
                e_ms_adj = None
            repaired_entries.append({
                "entry": entry, "_s_ms": s_ms_adj, "_e_ms": e_ms_adj,
                "_orig_index": i, "word": entry["word"], "start_off": start_off, "end_off": end_off,
            })
        else:
            repaired_entries.append({
                "entry": entry, "_s_ms": s_ms, "_e_ms": e_ms,
                "_orig_index": i, "word": entry["word"], "start_off": start_off, "end_off": end_off,
            })

    # Step 2: Coalesce zero-duration and missing offset entries
    kept_words: list[dict[str, Any]] = []
    skip_indexes: set[int] = set()
    num_repaired = len(repaired_entries)

    for i, item in enumerate(repaired_entries):
        orig_i = item["_orig_index"]
        if orig_i in skip_indexes:
            continue
        w_text = item["word"]
        s_ms = item["_s_ms"]
        e_ms = item["_e_ms"]
        start_off = item["start_off"]
        end_off = item["end_off"]

        if s_ms is not None and e_ms is not None:
            s = s_ms + win_start_ms
            e = e_ms + win_start_ms
            if e <= s or s < win_start_ms or e > win_end_ms + 250:
                would_repair += 1
                e = s + 80
                fixes.append(FixDecision(orig_i, "COALESCED_ZERO_DURATION_SEQUENCE", w_text, start_off, end_off, s, e))
        elif s_ms is not None and e_ms is None:
            would_repair += 1
            s = s_ms + win_start_ms
            merged_end_ms = None
            coalesced_words = [w_text]
            last_matched_orig_idx = orig_i
            for j in range(i + 1, min(i + 10, num_repaired)):
                nx = repaired_entries[j]
                if nx["start_off"] == start_off and nx["start_off"] is not None:
                    coalesced_words.append(nx["word"])
                    last_matched_orig_idx = nx["_orig_index"]
                    if nx["_e_ms"] is not None:
                        merged_end_ms = nx["_e_ms"] + win_start_ms
                        break
            if last_matched_orig_idx > orig_i:
                skip_indexes.update(range(orig_i + 1, last_matched_orig_idx + 1))
            w_text = "".join(coalesced_words)
            if merged_end_ms is None or merged_end_ms <= s:
                e = s + 80
            else:
                e = merged_end_ms
            fixes.append(FixDecision(orig_i, "COALESCED_ZERO_DURATION_SEQUENCE", w_text, start_off, end_off, s, e))
        elif s_ms is None and e_ms is not None and orig_i == 0:
            would_repair += 1
            e = e_ms + win_start_ms
            s = max(win_start_ms, e - 80)
            fixes.append(FixDecision(orig_i, "DEFAULT_HEAD_END_FILL", w_text, start_off, end_off, s, e))
        elif s_ms is None and e_ms is not None and orig_i > 0:
            unresolvable.append({"index": orig_i, "reason": "middle word missing startOffset", "entry": item["entry"]})
            would_repair += 1
            continue
        else:
            unresolvable.append({"index": orig_i, "reason": "no usable start or end", "entry": item["entry"]})
            would_repair += 1
            continue

        kept_words.append({"word": w_text, "start_ms": s, "end_ms": e, "_orig_index": orig_i})

    # Strict fail-closed check: corrupted unresolvable entries require manual review
    if unresolvable:
        raise V2Conflict(f"{window_id}: {len(unresolvable)} unresolvable entries require manual review")

    # Step 3: Stable sort by (start_ms, orig_index) to place out-of-order bursts in proper chronological sequence
    kept_words.sort(key=lambda w: (w["start_ms"], w["_orig_index"]))

    # Step 4: Strict monotonicity and non-overlapping boundary enforcement (zero token loss)
    clean_words: list[dict[str, Any]] = []
    prev_end = win_start_ms
    for w in kept_words:
        s = w["start_ms"]
        e = w["end_ms"]
        if s < prev_end:
            s = prev_end
        if e <= s:
            e = s + 80
        clean_words.append({"word": w["word"], "start_ms": s, "end_ms": e, "_orig_index": w["_orig_index"]})
        prev_end = e

    words = clean_words
    if n and (would_repair / n) > 0.5:
        raise V2Conflict(
            f"{window_id}: {would_repair}/{n} ({100*would_repair/n:.1f}%) entries would need repair; "
            f"exceeds 50% threshold -- manual review required"
        )
    return words, fixes, unresolvable


def parse_local_clock_fix_candidate(raw_bytes: bytes, window: dict[str, Any]) -> dict[str, Any]:
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if raw_sha != window.get("result_sha", raw_sha):
        raise V2Conflict(f"raw SHA mismatch: {raw_sha} != {window.get('result_sha')}")

    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, ValueError) as exc:
        raise V2Conflict("provider result JSON invalid") from exc

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise V2Conflict("Speech V2 result list missing")
    alts = results[0].get("alternatives") if isinstance(results[0], dict) else None
    if not isinstance(alts, list) or not alts:
        raise V2Conflict("Speech recognition alternatives missing")
    entries = alts[0].get("words") if isinstance(alts[0], dict) else None
    if not isinstance(entries, list) or not entries:
        raise V2Conflict("Speech word timing evidence missing")

    win_start_ms = int(window["start_ms"])
    win_end_ms = int(window["end_ms"])
    window_id = window.get("window_id", "window")

    words, fixes, unresolvable = _normalize_window(entries, win_start_ms, win_end_ms, window_id)
    if not words:
        raise V2Conflict(f"{window_id}: empty Chirp result requires acoustic review")

    manifest = {
        "schema": "golden-v2-chirp-window-v1",
        "job_id": window["job_id"],
        "operation_name": window["operation_name"],
        "request_id": window["request_id"],
        "window_id": window_id,
        "source_sha": window["source_sha"],
        "clip_sha": window["clip_sha"],
        "input_uri": window.get("gcs_uri", window.get("input_uri", "")),
        "output_uri": window["output_uri"],
        "start_ms": win_start_ms,
        "end_ms": win_end_ms,
        "raw_sha": raw_sha,
        "words": [{"word": w["word"], "start_ms": w["start_ms"], "end_ms": w["end_ms"]} for w in words],
        "actual_cents": int(window["actual_cents"]),
        "acoustic_gate": "NOT_RUN",
    }
    if fixes:
        manifest["timing_normalizations"] = [
            {
                "word": f.word,
                "start_ms": f.fixed_start_ms,
                "end_ms": f.fixed_end_ms,
                "method": f.method,
                "index": f.index,
                "original_startOffset": f.original_start,
                "original_endOffset": f.original_end,
            }
            for f in fixes
        ]

    window_sha = hashlib.sha256(_wire(manifest)).hexdigest()

    fix_proposal = {
        "schema": "golden-v2-chirp-local-clock-fix-v1",
        "job_id": window["job_id"],
        "window_id": window_id,
        "source_sha": window["source_sha"],
        "clip_sha": window["clip_sha"],
        "raw_sha": raw_sha,
        "raw_sha_unchanged": True,
        "actual_cents_unchanged": int(window["actual_cents"]),
        "window_sha256": window_sha,
        "submit_authorized": False,
        "provider_calls_started": False,
        "fixable_count": len(fixes),
        "unresolvable_count": len(unresolvable),
        "total_entries": len(entries),
        "fixable_fraction": (len(fixes) + len(unresolvable)) / max(1, len(entries)),
        "fixes": [
            {
                "index": f.index,
                "word": f.word,
                "method": f.method,
                "fixed_start_ms": f.fixed_start_ms,
                "fixed_end_ms": f.fixed_end_ms,
                "original_startOffset": f.original_start,
                "original_endOffset": f.original_end,
            }
            for f in fixes
        ],
        "unresolvable": unresolvable,
        "window_json_words": len(words),
    }
    proposal_sha = hashlib.sha256(_wire(fix_proposal)).hexdigest()

    return {
        "manifest": manifest,
        "fix_proposal": fix_proposal,
        "proposal_sha256": proposal_sha,
        "window_sha256": window_sha,
        "fixable_count": len(fixes),
        "unresolvable_count": len(unresolvable),
    }


def propose_local_clock_fix(
    *, ledger: GoldenV2Ledger, job_id: str, window_id: str, job_dir: Path
) -> dict[str, Any]:
    rows = [r for r in ledger.chirp_windows(job_id) if r["window_id"] == window_id]
    if not rows:
        raise V2Conflict(f"window {window_id} not found in ledger")
    row = dict(rows[0])
    if row["status"] != "REVIEW_REQUIRED":
        raise V2Conflict(f"window {window_id} is in status {row['status']!r}, not REVIEW_REQUIRED")

    raw_path = job_dir / "golden-v2" / "chirp-evidence" / window_id / "provider-raw.json"
    if not raw_path.is_file():
        raise V2Conflict(f"provider-raw.json missing for {window_id}")
    raw_bytes = raw_path.read_bytes()
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if raw_sha != row["result_sha"]:
        raise V2Conflict(f"provider-raw.json SHA mismatch: {raw_sha} != {row['result_sha']}")

    return parse_local_clock_fix_candidate(raw_bytes, row)


def apply_local_clock_fix(
    *,
    ledger: GoldenV2Ledger,
    job_id: str,
    window_id: str,
    job_dir: Path,
    proposal_sha: str,
    owner_token: str | None = None,
) -> dict[str, Any]:
    proposal = propose_local_clock_fix(ledger=ledger, job_id=job_id, window_id=window_id, job_dir=job_dir)
    if proposal["proposal_sha256"] != proposal_sha:
        raise V2Conflict(f"proposal SHA mismatch: {proposal['proposal_sha256']} != {proposal_sha}")

    manifest = proposal["manifest"]
    fix_proposal = proposal["fix_proposal"]
    root = job_dir / "golden-v2" / "chirp-evidence" / window_id

    window_bytes = _wire(manifest)
    proposal_bytes = _wire(fix_proposal)
    window_sha = hashlib.sha256(window_bytes).hexdigest()

    (root / "window.json").write_bytes(window_bytes)
    (root / f"chirp-local-clock-fix-{proposal_sha[:20]}.json").write_bytes(proposal_bytes)

    rows = [r for r in ledger.chirp_windows(job_id) if r["window_id"] == window_id]
    row = dict(rows[0])

    ledger.resolve_chirp_window_review(
        job_id,
        window_id,
        proposal_sha=proposal_sha,
        actual_cents=int(row["actual_cents"]),
        evidence_sha=row["result_sha"],
    )

    return {
        "job_id": job_id,
        "window_id": window_id,
        "status": "DONE",
        "proposal_sha256": proposal_sha,
        "window_sha256": window_sha,
        "actual_cents": row["actual_cents"],
        "words_emitted": len(manifest["words"]),
        "fixable_count": proposal["fixable_count"],
        "unresolvable_count": proposal["unresolvable_count"],
    }
