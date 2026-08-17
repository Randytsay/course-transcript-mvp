#!/usr/bin/env python3
"""Compare thinking-disabled M3 outputs with immutable prior baselines."""
from __future__ import annotations

import hashlib
import json
import os
from difflib import SequenceMatcher
from pathlib import Path

from app.providers.minimax_provider import MiniMaxCorrectionClient
from validate_m3_thinking_disabled import KEY_FILE, MODEL, load_baselines, load_source


OUTPUT = Path("/opt/course-transcript-source/data/m3-validation/phase-d-thinking-disabled-20260817/quality.json")


def compact(value: object) -> str:
    return "".join(str(value or "").split())


def ratio(left: str, right: str) -> float:
    return round(SequenceMatcher(None, compact(left), compact(right)).ratio(), 4)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    os.environ["MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS"] = "1"
    os.environ["MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS"] = "1"
    os.environ["MINIMAX_M3_TIMEOUT_SECONDS"] = "60"
    os.environ["MINIMAX_M3_MAX_OUTPUT_TOKENS"] = "4096"
    os.environ["MINIMAX_M3_CORRECTION_THINKING_MODE"] = "disabled"
    os.environ["MINIMAX_M3_TERMINOLOGY_THINKING_MODE"] = "adaptive"
    os.environ["MINIMAX_M3_REASONING_SPLIT"] = "true"

    source, source_file_sha256 = load_source()
    by_id = {str(item["segment_id"]): item for item in source}
    baselines = load_baselines()
    gemini_rows = baselines.get("gemini-3.7-flash", [])[:9]
    m3_rows = baselines.get("minimax-m3", [])
    client = MiniMaxCorrectionClient(key_file=KEY_FILE, model=MODEL, audit_dir=None)
    rows: list[dict[str, object]] = []

    for index, gemini_row in enumerate(gemini_rows):
        ids = [str(value) for value in gemini_row.get("source_segment_ids", [])]
        items = [by_id[sid] for sid in ids]
        previous_gemini = gemini_row.get("result", {})
        previous_m3_row = next((row for row in m3_rows if row.get("source_segment_ids") == ids), None)
        previous_m3 = previous_m3_row.get("result", {}) if isinstance(previous_m3_row, dict) else {}
        try:
            result = client.correct_window(items, [], context="")
            per_segment = []
            for item in items:
                sid = str(item["segment_id"])
                current = result[sid].get("corrected_text", "")
                raw = str(item["raw_text"])
                gemini = previous_gemini.get(sid, {}).get("corrected_text", "") if isinstance(previous_gemini, dict) else ""
                prior_m3 = previous_m3.get(sid, {}).get("corrected_text", "") if isinstance(previous_m3, dict) else ""
                per_segment.append({
                    "segment_id": sid,
                    "raw_len": len(compact(raw)),
                    "current_len": len(compact(current)),
                    "gemini_len": len(compact(gemini)),
                    "current_vs_raw_ratio": ratio(current, raw),
                    "current_vs_gemini_ratio": ratio(current, gemini),
                    "current_equals_gemini": current == gemini,
                    "current_equals_previous_m3": bool(prior_m3) and current == prior_m3,
                })
            rows.append({
                "window_index": index,
                "source_segment_ids": ids,
                "source_sha256": digest([{"segment_id": item["segment_id"], "raw_text": item["raw_text"]} for item in items]),
                "valid": True,
                "result_sha256": digest(result),
                "segments": per_segment,
            })
        except Exception as exc:
            rows.append({
                "window_index": index,
                "source_segment_ids": ids,
                "source_sha256": digest([{"segment_id": item["segment_id"], "raw_text": item["raw_text"]} for item in items]),
                "valid": False,
                "error_type": type(exc).__name__,
            })

    valid_rows = [row for row in rows if row.get("valid")]
    segment_rows = [segment for row in valid_rows for segment in row.get("segments", [])]
    summary = {
        "windows": len(rows),
        "valid_windows": len(valid_rows),
        "segments_compared": len(segment_rows),
        "current_equals_gemini_segments": sum(int(segment["current_equals_gemini"]) for segment in segment_rows),
        "current_equals_previous_m3_segments": sum(int(segment["current_equals_previous_m3"]) for segment in segment_rows),
        "mean_current_vs_gemini_ratio": round(sum(segment["current_vs_gemini_ratio"] for segment in segment_rows) / max(1, len(segment_rows)), 4),
        "min_current_vs_gemini_ratio": min((segment["current_vs_gemini_ratio"] for segment in segment_rows), default=None),
        "mean_current_vs_raw_ratio": round(sum(segment["current_vs_raw_ratio"] for segment in segment_rows) / max(1, len(segment_rows)), 4),
        "content_guard_or_empty_warning_segments": sum(int(segment["current_len"] == 0 or segment["current_len"] > 2.5 * max(1, segment["raw_len"])) for segment in segment_rows),
        "human_semantic_review": "NOT_AVAILABLE",
    }
    report = {
        "schema": "m3-thinking-disabled-quality-v1",
        "model": MODEL,
        "thinking_mode": "disabled",
        "source_file_sha256": source_file_sha256,
        "raw_immutable": True,
        "baseline": "phase-b-20260817/ab-10min-full",
        "rows": rows,
        "summary": summary,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
    return 0 if summary["valid_windows"] == summary["windows"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
