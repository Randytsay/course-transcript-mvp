"""Long-running provider-free worker for authorized local V2 stages."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.operations.runtime_heartbeat import write_service_heartbeat
from app.pipeline.golden_v2_worker import run_v2_worker_pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path,
        default=Path(os.environ.get(
            "COURSE_TRANSCRIPT_V2_LEDGER_PATH", "/app/data/golden-v2/ledger.sqlite",
        )),
    )
    parser.add_argument(
        "--artifact-root", type=Path,
        default=Path(os.environ.get(
            "COURSE_TRANSCRIPT_V2_ARTIFACT_ROOT", "/app/data/golden-v2",
        )),
    )
    parser.add_argument(
        "--worker-id", default=os.environ.get(
            "COURSE_TRANSCRIPT_V2_WORKER_ID", "golden-v2-worker",
        ),
    )
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 3600:
        parser.error("--poll-seconds must be between 1 and 3600")
    if not 1 <= args.max_jobs <= 1000:
        parser.error("--max-jobs must be between 1 and 1000")

    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    data_dir = args.artifact_root.parent
    service_state = "once" if args.once else "running"
    try:
        while not stop.is_set():
            write_service_heartbeat(data_dir, "golden-v2-worker", state=service_state)
            result = run_v2_worker_pass(
                database=args.database, artifact_root=args.artifact_root,
                worker_id=args.worker_id, max_jobs=args.max_jobs,
            )
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                  flush=True)
            if args.once:
                return 0 if result["action"] == "WORKER_CYCLE_COMPLETE" else 2
            if stop.wait(args.poll_seconds):
                break
    finally:
        write_service_heartbeat(data_dir, "golden-v2-worker", state="stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
