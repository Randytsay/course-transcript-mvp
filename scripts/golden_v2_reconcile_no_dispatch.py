#!/usr/bin/env python3
"""Reconcile one aged, zero-usage Gemini attempt proven absent from API metrics."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_no_dispatch_recovery import reconcile_primary_no_dispatch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
    args = parser.parse_args()
    if not args.project:
        parser.error("--project or GOOGLE_CLOUD_PROJECT is required")
    root = Path(args.artifact_root)
    database = Path(args.database)
    if root.is_symlink() or database.is_symlink() or database.resolve().parent != root.resolve():
        parser.error("isolated V2 ledger and artifact root required")
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    result = reconcile_primary_no_dispatch(
        ledger=GoldenV2Ledger.open_existing(database), job_id=args.job_id,
        artifact_root=root, project=args.project,
        session=AuthorizedSession(credentials),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
