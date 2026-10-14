"""Verify the offline real excerpt and record a non-executable USD 10 plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.golden_v2_live_smoke_plan import build_paid_smoke_plan, persist_plan_once


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-manifest", type=Path, required=True)
    parser.add_argument("--source-preflight", type=Path, required=True)
    parser.add_argument("--original-media", type=Path, required=True)
    parser.add_argument("--excerpt-audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cents", type=int, default=1000)
    args = parser.parse_args()
    try:
        plan = build_paid_smoke_plan(
            offline_manifest=args.offline_manifest,
            original_preflight=args.source_preflight,
            original_media=args.original_media,
            excerpt_audio=args.excerpt_audio,
            max_total_cents=args.max_cents,
        )
        persist_plan_once(args.output, plan)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(f"smoke plan blocked: {type(exc).__name__}")
    print(json.dumps({
        "schema": plan["schema"],
        "parent_v2_job_id": plan["parent_v2_job_id"],
        "clip_sha256": plan["clip_sha256"],
        "cap_usd_cents": plan["cap_usd_cents"],
        "paid_execution_enabled": False,
        "provider_calls_started": False,
        "next_action": plan["next_action"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())