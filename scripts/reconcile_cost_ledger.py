#!/usr/bin/env python3
"""Reconcile stale terminal-job reservations against recorded usage evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.jobs.store import JobStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.environ.get(
            "COURSE_TRANSCRIPT_DATABASE",
            "/app/data/course-transcript.db",
        ),
    )
    parser.add_argument("--actor", default="cost-ledger-reconciliation")
    args = parser.parse_args()
    store = JobStore(Path(args.database))
    print(
        json.dumps(
            store.reconcile_terminal_costs(actor=args.actor),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
