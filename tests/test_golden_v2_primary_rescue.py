import unittest
import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_primary_rescue import claim_independent_rescue, verify_primary_refusal


class IndependentRescueTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = GoldenV2Ledger(Path(self.temp.name) / 'ledger.sqlite')
        self.job = self.ledger.create(source_sha='a'*64, mode='FLASH_CHIRP',
            estimated_stage_cents=dict(gemini_primary=160, chirp_independent=50,
                compare=0, third_optional=0, adjudication=0, golden_qa=0))
        self.ledger.approve(self.job, revision=0, approved_cents=210, reference='owner', actor='owner')
        primary = self.ledger.claim_next(self.job, worker_id='test')
        self.ledger.hold_for_review(primary, evidence_sha='b'*64, actual_cents=24)
        self.args = dict(ledger=self.ledger, job_id=self.job, job_dir=Path(self.temp.name),
            revision=self.ledger.status(self.job)['revision'], source_sha='a'*64,
            cap_cents=210, reference='user-approved recovery',
            verify_authorization_and_live_source=lambda: True)

    def test_single_claim_preserves_primary_hold_cost_and_comparison_gate(self):
        with patch('app.pipeline.golden_v2_primary_rescue.verify_primary_refusal', return_value=True):
            claim = claim_independent_rescue(**self.args)
            self.ledger.assert_stage_claim(claim, stage='chirp_independent')
            with self.assertRaises(V2Conflict):
                claim_independent_rescue(**self.args)
        state = self.ledger.status(self.job)
        self.assertEqual(state['stages'][0]['state'], 'REVIEW_REQUIRED')
        self.assertEqual(state['stages'][0]['actual_cents'], 24)
        self.assertEqual(state['stages'][0]['evidence_sha'], 'b'*64)
        self.ledger.complete(claim, evidence_sha='c'*64, actual_cents=50)
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job, worker_id='cannot-bypass-review')

    def test_failed_verification_has_no_mutation(self):
        before = self.ledger.status(self.job)
        with patch('app.pipeline.golden_v2_primary_rescue.verify_primary_refusal', side_effect=V2Conflict('changed')):
            with self.assertRaises(V2Conflict):
                claim_independent_rescue(**self.args)
        self.assertEqual(before, self.ledger.status(self.job))

    def test_stale_revision_and_small_cap_fail_closed(self):
        for update in ({'revision':999}, {'cap_cents':209},
                       {'verify_authorization_and_live_source':lambda:False}):
            with self.assertRaises(V2Conflict):
                claim_independent_rescue(**{**self.args, **update})

    def test_unknown_paid_request_blocks_rescue(self):
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_stages SET state='OUTCOME_UNKNOWN' WHERE job_id=? AND name='gemini_primary'", (self.job,))
        with self.assertRaises(V2Conflict):
            claim_independent_rescue(**self.args)

    def test_raw_refusal_checksum_usage_and_source_are_required(self):
        root = Path(self.temp.name)
        (root/'golden-v2').mkdir()
        (root/'gemini-primary').mkdir()
        (root/'normalized.flac').write_bytes(b'local-test-audio')
        sha = hashlib.sha256(b'local-test-audio').hexdigest()
        identity = dict(source_sha256=sha, model='gemini-3.8-flash')
        raw = dict(identity=identity, status='REQUIRES_REVIEW', text='',
            usage_metadata=dict(prompt_token_count=100, candidates_token_count=0),
            raw_response=dict(prompt_feedback=dict(block_reason='SAFETY')))
        encoded = json.dumps(raw).encode()
        (root/'gemini-primary'/'raw.json').write_bytes(encoded)
        report = dict(job_id=self.job, source_sha256=sha, ready_for_chirp=False,
            golden_approved=False, actual_cents=1, window_evidence=[dict(identity=identity,
                raw_audit_sha256=hashlib.sha256(encoded).hexdigest())])
        encoded_report = json.dumps(report).encode()
        (root/'golden-v2'/'primary-stage.json').write_bytes(encoded_report)
        options = dict(job_dir=root, job_id=self.job, source_sha=sha,
            evidence_sha=hashlib.sha256(encoded_report).hexdigest(), actual_cents=1)
        self.assertTrue(verify_primary_refusal(**options))
        (root/'gemini-primary'/'raw.json').write_bytes(b'{}')
        with self.assertRaises(V2Conflict):
            verify_primary_refusal(**options)
