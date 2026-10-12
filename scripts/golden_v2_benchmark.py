"""Build a local paired-engine benchmark report from reviewed JSON evidence.

Input JSON has ``human_gold_windows`` and ``engine_outputs`` members matching
the schema of ``evaluate_paired_engine_benchmark``. The report path must be
new; this command performs no provider, network, database or Drive IO.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.evaluation.golden_v2_benchmark import evaluate_paired_engine_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="local JSON with reviewed gold and paired engine evidence")
    parser.add_argument("--output", type=Path, required=True,
                        help="new report path; existing files are never overwritten")
    args = parser.parse_args()
    try:
        payload: Any = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input must be a JSON object")
        report = evaluate_paired_engine_benchmark(
            payload.get("human_gold_windows"), payload.get("engine_outputs"),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(f"benchmark not written: {exc}")

    print(json.dumps({
        "report_path": str(args.output.resolve()),
        "window_count": report["window_count"],
        "engines": {
            name: {
                "cer": result["character_metrics"]["character_error_rate"],
                "cost_usd": result["cost_usd"],
                "elapsed_seconds_sum": result["elapsed_seconds_sum"],
            }
            for name, result in report["engines"].items()
        },
        "accuracy_claimed": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
