"""Read-only V2 paid-stage preflight: NEVER submits recognition or bills."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.golden_v2_paid_readiness import inspect_paid_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--max-approved-cents", type=int, default=1000)
    parser.add_argument("--expected-source-sha", default=None)
    args = parser.parse_args()
    try:
        report = inspect_paid_readiness(
            database=args.database,
            artifact_root=args.artifact_root,
            job_id=args.job_id,
            max_approved_cents=args.max_approved_cents,
            expected_source_sha=args.expected_source_sha,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["blockers"] else 2


if __name__ == "__main__":
    raise SystemExit(main())