"""Trusted operator CLI: resume V2 local stages, without cloud or Drive IO."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_coordinator import run_ready_local


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--worker-id", default="golden-v2-local")
    parser.add_argument("--recovery-age-seconds", type=int, default=300)
    args = parser.parse_args()
    if not args.database.is_file() or not args.job_dir.is_dir():
        parser.error("existing ledger and job artifact directory required")
    try:
        result = run_ready_local(
            ledger=GoldenV2Ledger(args.database), job_id=args.job_id,
            job_dir=args.job_dir, worker_id=args.worker_id,
            minimum_recovery_age_seconds=args.recovery_age_seconds,
        )
    except (V2Conflict, ValueError, OSError) as exc:
        parser.error(f"local V2 stage failed closed: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result[-1]["action"] not in {"BLOCKED", "RECOVERY_BLOCKED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
