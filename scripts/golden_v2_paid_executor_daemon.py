"""V2 provider controller; OFF unless enabled with trusted runtime services.

Enabling is not cost authorization. Each dispatch rechecks exact ledger owner
approval and independent provider/pricing evidence. This daemon has no built-in
credentials, permissive verifiers, or provider retry loop.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import signal
import sys
from threading import Event, Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.golden_v2_paid_executor import run_paid_executor_pass
from app.operations.runtime_heartbeat import write_service_heartbeat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--services-module', help='Owner-controlled module exporting paid_services/recovery_services')
    parser.add_argument('--aggregate-cap-cents', type=int, default=1000)
    parser.add_argument('--poll-seconds', type=float, default=30)
    parser.add_argument('--enable-paid', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 3600 or not 1 <= args.aggregate_cap_cents <= 100000:
        parser.error('bounded poll interval and aggregate cap required')
    services = None
    if args.enable_paid:
        if not args.services_module: parser.error('trusted service module required when enabled')
        services = importlib.import_module(args.services_module)
        if not callable(getattr(services, 'paid_services', None)) or not callable(getattr(services, 'recovery_services', None)):
            parser.error('trusted services must implement both factory contracts')
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    def heartbeat():
        while not stop.is_set():
            write_service_heartbeat(args.artifact_root.parent,'golden-v2-paid-executor',
                                    state='running' if args.enable_paid else 'disabled')
            stop.wait(15)
    thread = Thread(target=heartbeat,daemon=True); thread.start()
    try:
      while not stop.is_set():
        result = run_paid_executor_pass(database=args.database, artifact_root=args.artifact_root,
                                       enabled=args.enable_paid, aggregate_cap_cents=args.aggregate_cap_cents,
                                       paid_services=services.paid_services if services else None,
                                       recovery_services=services.recovery_services if services else None)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.once or stop.wait(args.poll_seconds): break
    finally:
        stop.set(); thread.join(timeout=5)
        write_service_heartbeat(args.artifact_root.parent,'golden-v2-paid-executor',state='stopped')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
