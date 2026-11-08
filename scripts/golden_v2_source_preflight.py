"""Stage a local source copy and create an unapproved Golden V2 ledger job."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs.golden_v2_ledger import MODES, STAGES
from app.pipeline.golden_v2_source_preflight import (
    DEFAULT_ESTIMATE_CEILING_CENTS, SourcePreflightError,
    register_source_preflight,
)


def _estimates(values: list[str]) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for value in values:
        name, separator, raw_amount = value.partition("=")
        if not separator or name not in STAGES or name in parsed:
            raise argparse.ArgumentTypeError("use each --estimate STAGE=CENTS once")
        try:
            amount = int(raw_amount)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("estimate must be integer USD cents") from exc
        if amount < 0:
            raise argparse.ArgumentTypeError("estimate cannot be negative")
        parsed[name] = amount
    if set(parsed) != set(STAGES):
        raise argparse.ArgumentTypeError("provide one estimate for every stage")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="already-downloaded local original media")
    parser.add_argument("--drive-observation", type=Path, required=True,
                        help="local JSON captured alongside the read-only download")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True,
                        help="isolated Golden V2 SQLite ledger")
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument(
        "--estimate-ceiling-cents", type=int,
        default=DEFAULT_ESTIMATE_CEILING_CENTS,
        help="local registration ceiling; default 1000 cents (USD 10)",
    )
    parser.add_argument(
        "--estimate", action="append", default=[], metavar="STAGE=CENTS",
        help="non-authorizing estimate; repeat once for every V2 stage",
    )
    args = parser.parse_args()
    try:
        estimates = _estimates(args.estimate)
        observation = json.loads(args.drive_observation.read_text(encoding="utf-8"))
        result = register_source_preflight(
            source_media=args.source,
            drive_observation=observation,
            artifact_root=args.artifact_root,
            database=args.database,
            mode=args.mode,
            estimated_stage_cents=estimates,
            estimate_ceiling_cents=args.estimate_ceiling_cents,
        )
    except (OSError, json.JSONDecodeError, argparse.ArgumentTypeError,
            SourcePreflightError, ValueError) as exc:
        parser.error(f"local source preflight failed closed: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
