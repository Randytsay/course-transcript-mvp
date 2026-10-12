"""Read-only paid dispatch review. NEVER executes Gemini, Chirp or GCS."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.golden_v2_paid_dispatch import preview_paid_dispatch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--trial-cap-cents", type=int, default=1000)
    parser.add_argument("--expected-source-sha")
    args = parser.parse_args()
    try:
        preview = preview_paid_dispatch(
            database=args.database, artifact_root=args.artifact_root,
            job_id=args.job_id,
            aggregate_trial_cap_cents=args.trial_cap_cents,
            expected_source_sha=args.expected_source_sha,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    return 0 if not preview["blockers"] else 2


if __name__ == "__main__":
    raise SystemExit(main())