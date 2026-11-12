"""Persistent owner-granted V2 sidecar delivery, independent of chat lifetime."""
import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
from contextlib import closing
from threading import Event, Thread
from time import monotonic

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.pipeline.golden_v2_delivery import publish_granted_outputs
from app.jobs.golden_v2_ledger import V2Conflict
from app.operations.runtime_heartbeat import write_service_heartbeat


def read_publication_state(database: Path, job_id: str) -> dict:
    """Read the job and QA state from one SQLite snapshot without a writer lock.

    GoldenV2Ledger.status() uses BEGIN IMMEDIATE, which must not be used in a
    periodic delivery scan: it can block active ASR and approval workers.
    The actual publisher independently rechecks every safety gate.
    """
    uri = database.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute(
            "SELECT j.status,j.revision,"
            " (SELECT s.state FROM v2_stages s WHERE s.job_id=j.id AND s.name='golden_qa')"
            " FROM v2_jobs j WHERE j.id=?",
            (job_id,),
        ).fetchone()
    if row is None:
        raise V2Conflict("publication job not found")
    return {"status": row[0], "revision": row[1],
            "stages": [{"name": "golden_qa", "state": row[2]}]}


def publication_gate(state: dict) -> str | None:
    """Provider-free check; a signed grant alone is never a Golden QA pass."""
    qa = next((stage for stage in state.get('stages', [])
               if stage.get('name') == 'golden_qa'), None)
    if state.get('status') != 'AWAITING_FINAL_REVIEW' or not qa or qa.get('state') != 'COMPLETE':
        return 'GOLDEN_QA_NOT_READY'
    return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root',type=Path,required=True)
    parser.add_argument('--lock-root',type=Path,required=True)
    parser.add_argument('--enable-publication',action='store_true')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(); stop=Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set()); signal.signal(signal.SIGINT,lambda *_:stop.set())
    def heartbeat():
        while not stop.is_set():
            write_service_heartbeat(args.artifact_root.parent,'golden-v2-delivery',state='running' if args.enable_publication else 'disabled')
            stop.wait(15)
    thread=Thread(target=heartbeat,daemon=True); thread.start()
    deferred: dict[str, str] = {}
    retry_after: dict[str, tuple[tuple[int, int], float]] = {}
    try:
        while not stop.is_set():
            if args.enable_publication:
                for grant in sorted((args.artifact_root/'jobs').glob('*/owner-publish-grant.json'))[:100]:
                    if stop.is_set(): break
                    if (grant.parent/'golden-v2/drive-delivery.json').is_file(): continue
                    fingerprint = (0, 0)
                    try:
                        job_id = grant.parent.name
                        state = read_publication_state(args.artifact_root / 'ledger.sqlite', job_id)
                        reason = publication_gate(state)
                        if reason:
                            if deferred.get(job_id) != reason:
                                print(json.dumps({'job_id':job_id,'action':'PUBLICATION_DEFERRED',
                                                  'reason':reason}),flush=True)
                            deferred[job_id] = reason
                            continue
                        deferred.pop(job_id, None)
                        # A failed immutable/Drive reconciliation must not be
                        # retried every 30s. Changes to the owner grant or
                        # ledger revision invalidate the cooldown immediately.
                        fingerprint = (int(state['revision']), grant.stat().st_mtime_ns)
                        previous = retry_after.get(job_id)
                        if previous and previous[0] == fingerprint and monotonic() < previous[1]:
                            continue
                        result=publish_granted_outputs(root=args.artifact_root,job_id=grant.parent.name,lock_root=args.lock_root)
                        retry_after.pop(job_id, None)
                        print(json.dumps(result,ensure_ascii=False),flush=True)
                    except V2Conflict as exc:
                        retry_after[grant.parent.name] = (fingerprint, monotonic() + 300)
                        print(json.dumps({'job_id':grant.parent.name,'action':'PUBLICATION_HELD',
                                          'error_type':type(exc).__name__,'retry_after_seconds':300}),flush=True)
                    except Exception as exc:
                        print(json.dumps({'job_id':grant.parent.name,'action':'PUBLICATION_HELD',
                                          'error_type':type(exc).__name__}),flush=True)
            if args.once or stop.wait(30): break
    finally:
        stop.set(); thread.join(timeout=5)
        write_service_heartbeat(args.artifact_root.parent,'golden-v2-delivery',state='stopped')
    return 0


if __name__=='__main__': raise SystemExit(main())
