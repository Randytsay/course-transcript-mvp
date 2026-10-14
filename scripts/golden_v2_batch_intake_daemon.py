"""Drive-read-only, non-paid V2 batch intake daemon, independent of chat lifetime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.golden_v2_batch_intake import run_batch_intake_pass
from app.pipeline.golden_v2_quote_intake import run_quote_intake_pass
from app.operations.runtime_heartbeat import write_service_heartbeat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("COURSE_TRANSCRIPT_V2_LEDGER_PATH", "/app/data/golden-v2/ledger.sqlite")))
    parser.add_argument("--artifact-root", type=Path, default=Path(os.environ.get("COURSE_TRANSCRIPT_V2_ARTIFACT_ROOT", "/app/data/golden-v2")))
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 3600:
        parser.error("poll interval must be between 1 and 3600 seconds")
    ceiling = int(os.environ.get("COURSE_TRANSCRIPT_V2_MAX_APPROVAL_CENTS", "1000"))
    if ceiling <= 0:
        parser.error("positive intake estimate ceiling required")
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    def heartbeat():
        while not stop.is_set():
            write_service_heartbeat(args.artifact_root.parent, 'golden-v2-intake')
            stop.wait(15)
    heartbeat_thread = Thread(target=heartbeat, name='intake-heartbeat', daemon=True)
    heartbeat_thread.start()
    try:
        while not stop.is_set():
            result = run_quote_intake_pass(database=args.database, artifact_root=args.artifact_root,
                                           ceiling_cents=ceiling)
            if result['action'] == 'QUOTE_INTAKE_IDLE':
                result = run_batch_intake_pass(database=args.database, artifact_root=args.artifact_root,
                                              ceiling_cents=ceiling)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                return 0
            if result["action"] != "SOURCE_PREFLIGHT_COMPLETE" and stop.wait(args.poll_seconds):
                break
    finally:
        stop.set()
        heartbeat_thread.join(timeout=5)
        write_service_heartbeat(args.artifact_root.parent, "golden-v2-intake", state="stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
