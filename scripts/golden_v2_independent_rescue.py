"""Exact-source operator rescue; one submission, then saved-operation recovery."""
import argparse
import json
from pathlib import Path
import sys
from threading import Event
import signal
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.jobs.drive_api import read_drive_media_metadata
from app.pipeline.golden_v2_primary_rescue import claim_independent_rescue
from app.pipeline.golden_v2_runtime_services import paid_services, recovery_services
from app.pipeline.golden_v2_chirp_stage import plan_approved_chirp, submit_planned_chirp
from app.pipeline.golden_v2_chirp_recovery_worker import run_chirp_recovery_pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--revision', type=int, required=True)
    parser.add_argument('--cap-cents', type=int, required=True)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    ledger = GoldenV2Ledger.open_existing(root/'ledger.sqlite')
    job_dir = root/'jobs'/args.job_id
    if job_dir.resolve().parent != root/'jobs' or job_dir.is_symlink():
        raise V2Conflict('invalid job directory')
    state = ledger.status(args.job_id)
    if state['source_sha'] != args.source_sha:
        raise V2Conflict('source changed')
    snapshot = dict(job_id=args.job_id, source_sha256=args.source_sha,
                    revision=args.revision, stage='chirp_independent',
                    aggregate_cap_cents=args.cap_cents)
    if args.submit:
        services = paid_services(snapshot)
        pre = json.loads((job_dir/'source-preflight.json').read_text())
        def verified():
            preview = dict(source_sha256=args.source_sha)
            if services['verify_owner'](preview) is not True or services['verify_capability_and_pricing'](preview) is not True:
                return False
            live = read_drive_media_metadata(pre['drive_source_path'])
            observed = pre['drive_observation']
            return (live['id'] == observed['file_id']
                    and str(live['version']) == str(observed['version'])
                    and live['size_bytes'] == observed['size_bytes']
                    and live['md5_checksum'] == pre['original_md5_checksum'])
        claim = claim_independent_rescue(ledger=ledger, job_id=args.job_id,
            job_dir=job_dir, revision=args.revision, source_sha=args.source_sha,
            cap_cents=args.cap_cents, reference=args.reference,
            verify_authorization_and_live_source=verified)
        options = dict(services['stage_options'])
        gateway = options.pop('gateway')
        plan_approved_chirp(ledger=ledger, claim=claim, normalized_audio=job_dir/'normalized.flac',
                           job_dir=job_dir, **options)
        rows = submit_planned_chirp(ledger=ledger, claim=claim, gateway=gateway)
        print(json.dumps(dict(action='SUBMITTED_ONCE', operations=len(rows))), flush=True)
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    deadline = time.monotonic()+24*3600
    while not stop.is_set() and time.monotonic()<deadline:
        result = run_chirp_recovery_pass(ledger=ledger, job_id=args.job_id, job_dir=job_dir,
                                        **recovery_services(snapshot))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if result['action'] != 'WAITING_FOR_EXISTING_OPERATIONS':
            break
        stop.wait(120)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
