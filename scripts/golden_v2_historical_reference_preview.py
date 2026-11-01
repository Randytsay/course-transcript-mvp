"""Offline/isolated historical TXT/SRT-to-gap audit. Does not touch Drive.

Example:
  python scripts/golden_v2_historical_reference_preview.py \\
    --plan /secure/plan.json --gap-manifest /secure/26-gaps.json \\
    --gap-manifest-sha <pinned-sha> --job-id <pinned-id> \\
    --source-sha <pinned-normalized-flac-sha> --output /private/new-report.json

The operator MUST independently obtain fresh Drive file IDs, versions and
complete immutable bytes for the exact job from an authorized read-only source.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.pipeline.golden_v2_historical_reference_preview import preview_historical_references


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--gap-manifest', required=True, type=Path)
    parser.add_argument('--gap-manifest-sha', required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if (args.plan.is_symlink() or args.gap_manifest.is_symlink()
            or args.output.is_symlink() or args.output.exists()
            or not args.output.parent.is_dir()
            or args.output.parent.resolve() == args.gap_manifest.parent.resolve()):
        parser.error('reference preview inputs/output must be isolated, regular and create-only')
    gap_raw = args.gap_manifest.read_bytes()
    report = preview_historical_references(
        plan=json.loads(args.plan.read_text(encoding='utf-8')),
        gap_manifest=json.loads(gap_raw), gap_manifest_bytes=gap_raw,
        expected_gap_manifest_sha256=args.gap_manifest_sha,
        expected_job_id=args.job_id, expected_source_sha256=args.source_sha,
        plan_directory=args.plan.parent)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode()
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    print(json.dumps({'report_written': True, 'gap_count': len(report['gaps']),
                      'counts': report['counts'], 'golden_qa_pass': False,
                      'provider_calls_started': False, 'drive_writes_started': False}))


if __name__ == '__main__':
    main()
