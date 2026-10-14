"""Create/read a one-clip no-retry trial ledger. NO PAID provider entrypoint.

Use --init exactly once, with a non-secret owner approval reference. A paid
executor must separately verify this approval and cloud capabilities.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.golden_v2_smoke_ledger import SingleClipSmokeLedger, SmokeConflict

PARENT_JOB = "v2p_b075478d5d505ee49c330016c6d12023"


def trial_from_root(root: Path) -> SingleClipSmokeLedger:
    if root.is_symlink():
        raise SmokeConflict("linked trial root")
    root = root.expanduser().resolve()
    return SingleClipSmokeLedger(
        db=root / "smoke" / "clip-trial.sqlite",
        plan_path=root / "smoke" / "approved-smoke-scope-pending-cloud.json",
        offline_manifest=root / "smoke" / "real-audio-offline-smoke.json",
        source_preflight=root / "golden-v2" / "jobs" / PARENT_JOB / "source-preflight.json",
        original_media=root / "input" / "source-original.mp3",
        excerpt_audio=root / "smoke" / "real-excerpt-4min.flac",
        receipt_root=root / "smoke" / "receipts",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="Independent real-audio trial DATA root")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--init", action="store_true")
    action.add_argument("--status", action="store_true")
    parser.add_argument("--owner-reference", default=None,
                        help="Owner authorization record identifier, not a token")
    args = parser.parse_args()
    try:
        trial = trial_from_root(args.root)
        if args.init:
            if not args.owner_reference:
                parser.error("--owner-reference required for --init")
            result = trial.initialize(owner_reference=args.owner_reference)
        else:
            result = trial.status()
    except (SmokeConflict, OSError, ValueError) as exc:
        parser.error(f"trial blocked: {type(exc).__name__}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
