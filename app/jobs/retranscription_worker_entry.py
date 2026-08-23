"""Production-safe entry point for the paid retranscription worker.

The feature is intentionally disabled by default until the ARM64/VPS live gate
has validated real Chirp credentials, restart recovery, and artifact isolation.
Direct unit/integration tests may still call ``retranscription_worker.run_once``
without this deployment gate.
"""
from __future__ import annotations

import os
import time

from app.jobs import retranscription_worker


def enabled() -> bool:
    return os.environ.get("ASR_RETRANSCRIPTION_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def main() -> int:
    if enabled():
        return retranscription_worker.main()

    poll_seconds = max(
        5.0,
        float(os.environ.get("COURSE_TRANSCRIPT_RETRANSCRIPTION_DISABLED_POLL_SECONDS", "60")),
    )
    print(
        "RETRANSCRIPTION_WORKER=DISABLED "
        "reason=ASR_RETRANSCRIPTION_ENABLED_not_true",
        flush=True,
    )
    while True:
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
