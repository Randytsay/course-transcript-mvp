#!/usr/bin/env python3
"""Add the required representative windows to an existing Phase-D run."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.providers.minimax_provider import MiniMaxCorrectionClient
from validate_m3_thinking_disabled import BASELINE, KEY_FILE, MODEL, OUTPUT, load_baselines, load_source, run_window


def main() -> int:
    os.environ["MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS"] = "1"
    os.environ["MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS"] = "1"
    os.environ["MINIMAX_M3_TIMEOUT_SECONDS"] = "60"
    os.environ["MINIMAX_M3_MAX_OUTPUT_TOKENS"] = "4096"
    os.environ["MINIMAX_M3_CORRECTION_THINKING_MODE"] = "disabled"
    os.environ["MINIMAX_M3_TERMINOLOGY_THINKING_MODE"] = "adaptive"
    os.environ["MINIMAX_M3_REASONING_SPLIT"] = "true"

    report = json.loads(OUTPUT.read_text(encoding="utf-8"))
    records = list(report["records"])
    if sum(record.get("label") == "phase-c-replay" for record in records) != 11:
        raise RuntimeError("existing Phase-C replay is not the expected 11-window set")
    if any(record.get("label") == "phase-b-representative" for record in records):
        print(json.dumps({"status": "already_extended", "records": len(records)}, ensure_ascii=False))
        return 0

    items, source_hash = load_source()
    baselines = load_baselines()
    source_by_id = {str(item["segment_id"]): item for item in items}
    client = MiniMaxCorrectionClient(key_file=KEY_FILE, model=MODEL, audit_dir=None)
    extra_records = []
    for index, prior_row in enumerate(baselines.get("gemini-3.7-flash", [])[:9]):
        prior_ids = prior_row.get("source_segment_ids", [])
        if not isinstance(prior_ids, list) or not prior_ids or any(str(sid) not in source_by_id for sid in prior_ids):
            continue
        extra_items = [source_by_id[str(sid)] for sid in prior_ids]
        extra_records.append(
            run_window(
                client=client,
                items=extra_items,
                baselines=baselines,
                label="phase-b-representative",
                offset=index,
                window_size=len(extra_items),
            )
        )
    records.extend(extra_records)
    representative = [
        record for record in records if record["label"] in {"phase-c-replay", "phase-b-representative"}
    ]
    summary = {
        "phase_c_replay_windows": 11,
        "additional_representative_windows": len(extra_records),
        "total_windows": len(representative),
        "valid_windows": sum(int(bool(record.get("valid"))) for record in representative),
        "output_limit_hits": sum(int(record.get("finish_reason") == "length" or record.get("error", {}).get("kind") == "output_limit") for record in representative),
        "transport_errors": sum(int(record.get("error", {}).get("kind") == "transient_exhausted") for record in representative),
        "max_latency_ms": max(int(record.get("latency_ms") or 0) for record in records),
    }
    report["records"] = records
    report["summary"] = summary
    report["representative_gate"] = {
        "valid_rate": summary["valid_windows"] / max(1, summary["total_windows"]),
        "requires_all_valid": True,
        "passed": summary["total_windows"] >= 20 and summary["valid_windows"] == summary["total_windows"] and summary["output_limit_hits"] == 0 and summary["transport_errors"] == 0,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "representative_gate": report["representative_gate"]}, ensure_ascii=False, indent=2))
    return 0 if report["representative_gate"]["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
