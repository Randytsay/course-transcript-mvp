from __future__ import annotations

import fcntl
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_golden_v2_drive_revision as fixture
from app.pipeline import golden_v2_paid_executor as executor
from app.pipeline import golden_v2_trusted_dispatch as dispatch
from app.jobs.golden_v2_ledger import V2Conflict


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.DriveRevisionTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger
        self.calls = 0

    def services(self, snapshot):
        return {'verify_owner': lambda _: True, 'verify_capability_and_pricing': lambda _: True,
                'read_metadata': self.fixture.live,
                'stage_options': {'storage_client': object(), 'vertex_client': object(),
                                  'bucket_name': 'private-test', 'object_prefix': 'v2-only',
                                  'cost_estimator': lambda _: 30, 'cost_accountant': lambda _: 25}}

    def run_pass(self, **options):
        args = dict(database=self.fixture.db, artifact_root=self.fixture.root, enabled=True,
                    paid_services=self.services, recovery_services=lambda _: {})
        args.update(options)
        return executor.run_paid_executor_pass(**args)

    def complete(self, **kwargs):
        self.calls += 1
        kwargs['ledger'].complete(kwargs['claim'], evidence_sha='a'*64, actual_cents=25)
        return {}

    def test_disabled_never_opens_db_or_constructs_provider(self):
        def fail(_): self.fail('factory forbidden')
        result = executor.run_paid_executor_pass(database=Path('/does-not-exist'), artifact_root=Path('/does-not-exist'),
                                                 paid_services=fail, recovery_services=fail)
        self.assertEqual(result['action'], 'PAID_EXECUTOR_DISABLED')

    def test_success_advances_once_and_reuses_complete_primary(self):
        with patch.object(dispatch, 'execute_approved_primary_stage', autospec=True, side_effect=self.complete):
            result = self.run_pass()
        self.assertEqual(self.calls, 1)
        self.assertEqual(result['outcomes'][0]['action'], 'PAID_STAGE_DISPATCH_RETURNED')
        self.assertEqual(self.ledger.status(self.fixture.job)['stages'][0]['state'], 'COMPLETE')
        # Next pass sees Chirp, never primary again; malformed services hold.
        with patch.object(dispatch, 'execute_approved_primary_stage', autospec=True, side_effect=self.complete):
            self.run_pass()
        self.assertEqual(self.calls, 1)

    def test_timeout_retains_claim_and_next_cycle_never_reissues(self):
        with patch.object(dispatch, 'execute_approved_primary_stage', autospec=True, side_effect=TimeoutError('private upstream error')) as provider:
            first = self.run_pass()
            second = self.run_pass()
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(first['outcomes'][0]['action'], 'JOB_REQUIRES_RECONCILIATION')
        self.assertEqual(second['outcomes'][0]['action'], 'REVIEW_OR_RECONCILIATION_REQUIRED')
        self.assertNotIn('private upstream', str(first))

    def test_owner_or_capability_denial_does_not_claim(self):
        before = self.ledger.status(self.fixture.job)
        for field in ['verify_owner', 'verify_capability_and_pricing']:
            def factory(snapshot): return {**self.services(snapshot), field: lambda _: False}
            self.assertEqual(self.run_pass(paid_services=factory)['outcomes'][0]['action'], 'JOB_REQUIRES_RECONCILIATION')
            self.assertEqual(self.ledger.status(self.fixture.job), before)

    def test_worker_lock_and_exact_source_rejection(self):
        with (self.fixture.root / 'paid-executor.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_pass()['action'], 'PAID_EXECUTOR_BUSY')
        def wrong(snapshot): return {**self.services(snapshot), 'read_metadata': lambda _: {**self.fixture.live(None), 'version': 'changed'}}
        before = self.ledger.status(self.fixture.job)
        self.run_pass(paid_services=wrong)
        self.assertEqual(self.ledger.status(self.fixture.job), before)

    def test_runtime_factory_cannot_replace_cas_identity(self):
        def invalid(snapshot): return {**self.services(snapshot), 'expected_source_sha': 'b'*64}
        before = self.ledger.status(self.fixture.job)
        self.run_pass(paid_services=invalid)
        self.assertEqual(self.ledger.status(self.fixture.job), before)

    def test_provider_free_factory_does_not_authorize_unknown_or_review_states(self):
        state = self.ledger.status(self.fixture.job)
        claim = self.ledger.claim_next(self.fixture.job, worker_id='prior')
        self.ledger.mark_outcome_unknown(claim)
        result = self.run_pass(paid_services=lambda _: self.fail('unknown outcome cannot dispatch'))
        self.assertEqual(result['outcomes'], [])

    def test_held_gemini_is_invisible_until_explicit_auto_rescue(self):
        from app.jobs.golden_v2_read import list_v2_rescue_jobs
        claim = self.ledger.claim_next(self.fixture.job, worker_id='known-provider')
        self.ledger.hold_for_review(claim, evidence_sha='f'*64, actual_cents=5)
        self.assertEqual(len(list_v2_rescue_jobs(self.fixture.db)), 1)
        self.assertEqual(self.run_pass()['outcomes'], [])

    def test_known_refusal_auto_rescue_uses_exact_grant_and_submits_once(self):
        claim = self.ledger.claim_next(self.fixture.job, worker_id='known-provider')
        self.ledger.hold_for_review(claim, evidence_sha='f'*64, actual_cents=5)
        sentinel = object()
        options = dict(verify_owner=lambda _: True,
                       verify_capability_and_pricing=lambda _: True,
                       stage_options={'gateway': sentinel, 'bucket_name': 'verified'})
        with (patch.object(executor, 'verify_same_audio_revision', return_value=True),
              patch.object(executor, 'claim_independent_rescue', return_value={'stage': 'chirp_independent'}) as claim_rescue,
              patch.object(executor, 'plan_approved_chirp') as planned,
              patch.object(executor, 'submit_planned_chirp') as submitted):
            result = self.run_pass(
                auto_rescue_known_refusal=True, paid_services=lambda _: options)
        self.assertEqual(result['outcomes'][0]['action'], 'AUTO_INDEPENDENT_CHIRP_SUBMITTED_ONCE')
        self.assertEqual(claim_rescue.call_count, 1)
        self.assertEqual(claim_rescue.call_args.kwargs['cap_cents'], 1000)
        self.assertEqual(planned.call_count, 1)
        self.assertEqual(submitted.call_count, 1)
        self.assertIs(submitted.call_args.kwargs['gateway'], sentinel)

    def test_known_refusal_rejects_unknown_or_stale_source_without_dispatch(self):
        claim = self.ledger.claim_next(self.fixture.job, worker_id='known-provider')
        self.ledger.hold_for_review(claim, evidence_sha='f'*64, actual_cents=5)
        options = dict(verify_owner=lambda _: True,
                       verify_capability_and_pricing=lambda _: True,
                       stage_options={'gateway': object()})
        with (patch.object(executor, 'verify_same_audio_revision', return_value=False),
              patch.object(executor, 'claim_independent_rescue', side_effect=lambda **_: (_ for _ in ()).throw(V2Conflict('not authorized'))) as rescue,
              patch.object(executor, 'submit_planned_chirp') as submit):
            result = self.run_pass(auto_rescue_known_refusal=True,
                                   paid_services=lambda _: options)
        self.assertEqual(result['outcomes'][0]['action'], 'JOB_REQUIRES_RECONCILIATION')
        self.assertEqual(rescue.call_count, 1)
        self.assertFalse(submit.called)

    def test_saved_rescue_lro_recovery_does_not_submit_or_claim(self):
        claim = self.ledger.claim_next(self.fixture.job, worker_id='known-provider')
        self.ledger.hold_for_review(claim, evidence_sha='f'*64, actual_cents=5)
        held = self.ledger.status(self.fixture.job)
        held['status'] = 'PROCESSING'
        held['stages'][1]['state'] = 'RUNNING'
        with (patch.object(executor.GoldenV2Ledger, 'open_existing', return_value=self.ledger),
              patch.object(self.ledger, 'status', return_value=held),
              patch.object(executor, 'list_v2_rescue_jobs', return_value=[
                  {'job_id': self.fixture.job, 'status': 'PROCESSING'}]),
              patch.object(executor, 'run_ready_local') as local,
              patch.object(executor, 'run_chirp_recovery_pass', return_value={
                  'action': 'WAITING_FOR_EXISTING_OPERATIONS'}) as recovery,
              patch.object(executor, 'submit_planned_chirp') as submit):
            result = self.run_pass(auto_rescue_known_refusal=True,
                                   paid_services=lambda _: self.fail('no paid dispatch'),
                                   recovery_services=lambda _: {
                                       'gateway': object(), 'cost_accountant': lambda _: 3})
        self.assertEqual(result['outcomes'][0]['action'], 'WAITING_FOR_EXISTING_OPERATIONS')
        self.assertEqual(recovery.call_count, 1)
        local.assert_not_called()
        submit.assert_not_called()

    def test_chirp_recovery_uses_saved_operation_path_and_not_submit(self):
        with patch.object(dispatch, 'execute_approved_primary_stage', autospec=True, side_effect=self.complete): self.run_pass()
        self.ledger.claim_next(self.fixture.job, worker_id='prior-chirp')
        sentinel = {'gateway': object(), 'cost_accountant': lambda _: 2}
        with patch.object(executor, 'run_chirp_recovery_pass', return_value={'job_id': self.fixture.job, 'action': 'WAITING_FOR_EXISTING_OPERATIONS'}) as recover:
            result = self.run_pass(paid_services=lambda _: self.fail('recovery cannot resubmit'), recovery_services=lambda _: sentinel)
        self.assertEqual(recover.call_count, 1)
        self.assertIs(recover.call_args.kwargs['gateway'], sentinel['gateway'])
        self.assertEqual(result['outcomes'][0]['action'], 'WAITING_FOR_EXISTING_OPERATIONS')
