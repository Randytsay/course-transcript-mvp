"""Persistent-controller pass for separately authorized V2 provider services.

Services are injected by a trusted runtime, never browser input. This module
creates no credentials, grants no approval, and defaults to disabled. A stage
with an unknown outcome is held; saved Chirp LROs use the existing GET recovery
path. Completed stages are reused by the local coordinator.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.jobs.golden_v2_read import list_v2_active_jobs
from app.pipeline.golden_v2_chirp_recovery_worker import run_chirp_recovery_pass
from app.pipeline.golden_v2_coordinator import run_ready_local
from app.pipeline.golden_v2_trusted_dispatch import execute_trusted_paid_stage


def run_paid_executor_pass(*, database: Path, artifact_root: Path,
                           enabled: bool = False, aggregate_cap_cents: int = 1000,
                           paid_services: Callable[[dict], dict] | None = None,
                           recovery_services: Callable[[dict], dict] | None = None,
                           worker_id: str = 'golden-v2-paid-executor', max_jobs: int = 100) -> dict:
    if type(enabled) is not bool:
        raise ValueError('explicit executor flag required')
    if not enabled:
        return {'action': 'PAID_EXECUTOR_DISABLED', 'outcomes': [], 'provider_calls_started': False}
    if (type(aggregate_cap_cents) is not int or not 1 <= aggregate_cap_cents <= 100000
            or type(max_jobs) is not int or not 1 <= max_jobs <= 1000
            or not worker_id.strip() or not callable(paid_services) or not callable(recovery_services)):
        raise ValueError('bounded budget and trusted service factories required')
    root = Path(artifact_root).expanduser()
    path = Path(database).expanduser()
    if root.is_symlink() or path.is_symlink() or not root.is_dir() or path.resolve().parent != root.resolve():
        raise ValueError('isolated executor ledger required')
    root = root.resolve()
    ledger = GoldenV2Ledger.open_existing(path)
    fd = os.open(root / 'paid-executor.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'action': 'PAID_EXECUTOR_BUSY', 'outcomes': [], 'provider_calls_started': False}
        outcomes = []
        for job in list_v2_active_jobs(path, limit=max_jobs):
            job_id = job['job_id']
            try:
                jobs_root = root / 'jobs'
                job_dir = jobs_root / job_id
                if (jobs_root.is_symlink() or job_dir.is_symlink() or not job_dir.is_dir()
                        or job_dir.resolve().parent != jobs_root or job_dir.resolve() != job_dir):
                    raise V2Conflict('source artifact directory unavailable')
                local = run_ready_local(ledger=ledger, job_id=job_id, job_dir=job_dir, worker_id=worker_id)
                state = ledger.status(job_id)
                pending = next((s for s in state['stages'] if s['state'] not in {'COMPLETE', 'SKIPPED'}), None)
                if not pending or state['status'] not in {'QUEUED', 'PROCESSING'}:
                    outcomes.append({'job_id': job_id, 'action': local[-1]['action']})
                    continue
                stage = pending['name']
                snapshot = {'job_id': job_id, 'source_sha256': state['source_sha'],
                            'revision': state['revision'], 'stage': stage,
                            'aggregate_cap_cents': aggregate_cap_cents}
                if stage == 'chirp_independent' and pending['state'] == 'RUNNING':
                    options = recovery_services(dict(snapshot))
                    if not isinstance(options, dict) or set(options) - {'gateway', 'cost_accountant', 'max_operations', 'acoustic_inspector'}:
                        raise V2Conflict('invalid recovery service contract')
                    result = run_chirp_recovery_pass(ledger=ledger, job_id=job_id, job_dir=job_dir, **options)
                    outcomes.append(result)
                elif stage in {'gemini_primary', 'chirp_independent', 'third_optional'} and pending['state'] == 'PENDING':
                    options = paid_services(dict(snapshot))
                    if (not isinstance(options, dict) or set(options) - {'verify_owner', 'verify_capability_and_pricing', 'stage_options', 'read_metadata'}):
                        raise V2Conflict('invalid paid service contract')
                    # This guard independently rereads approval, source, budget,
                    # live Drive version and prices before a SQLite CAS claim.
                    result = execute_trusted_paid_stage(
                        database=path, artifact_root=root, job_id=job_id,
                        expected_revision=state['revision'], expected_source_sha=state['source_sha'],
                        expected_stage=stage, aggregate_cap_cents=aggregate_cap_cents,
                        worker_id=worker_id, **options)
                    outcomes.append({'job_id': job_id, 'stage': stage,
                                     'action': 'PAID_STAGE_DISPATCH_RETURNED', 'status': result['status']})
                else:
                    outcomes.append({'job_id': job_id, 'stage': stage,
                                     'action': 'REVIEW_OR_RECONCILIATION_REQUIRED', 'stage_state': pending['state']})
            except Exception:
                # A failure AFTER dispatch can still be billable. The original
                # claim/raw barrier remains; the next cycle cannot re-claim it.
                outcomes.append({'job_id': job_id, 'action': 'JOB_REQUIRES_RECONCILIATION'})
        return {'action': 'PAID_EXECUTOR_CYCLE_COMPLETE', 'outcomes': outcomes,
                'provider_calls_started': 'CHECK_IMMUTABLE_STAGE_AUDITS', 'drive_mutation_started': False}
