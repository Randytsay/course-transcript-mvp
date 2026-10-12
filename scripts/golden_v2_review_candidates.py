"""Prepare or apply a local audio review bundle for V2 comparison candidates.

No network/provider/Drive access occurs. Applying decisions requires an
explicit operator listening attestation and records that attestation as such.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_review_bundle import (
    apply_review_bundle, inspect_review_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="write a source-bound review template")
    inspect.add_argument("--database", type=Path, required=True)
    inspect.add_argument("--job-id", required=True)
    inspect.add_argument("--job-dir", type=Path, required=True)
    inspect.add_argument("--normalized-audio", type=Path)
    inspect.add_argument("--output", type=Path,
                         help="new JSON file; existing files are never overwritten")

    apply = commands.add_parser("apply", help="record listened candidate decisions")
    apply.add_argument("--database", type=Path, required=True)
    apply.add_argument("--job-id", required=True)
    apply.add_argument("--job-dir", type=Path, required=True)
    apply.add_argument("--normalized-audio", type=Path, required=True)
    apply.add_argument("--bundle", type=Path, required=True)
    apply.add_argument("--reviewer", required=True)
    apply.add_argument("--review-reference", required=True)
    apply.add_argument("--confirm-listened", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "inspect":
            result = inspect_review_bundle(
                database=args.database, job_id=args.job_id,
                job_dir=args.job_dir, normalized_audio=args.normalized_audio,
            )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("x", encoding="utf-8") as handle:
                    json.dump(result, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
        else:
            bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
            result = apply_review_bundle(
                database=args.database, job_id=args.job_id,
                job_dir=args.job_dir, normalized_audio=args.normalized_audio,
                bundle=bundle, reviewer=args.reviewer,
                review_reference=args.review_reference,
                confirm_listened=args.confirm_listened,
            )
    except (V2Conflict, ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.error(f"review remains blocked: {exc}")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "apply" and result.get("status") != "COMPLETE":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
