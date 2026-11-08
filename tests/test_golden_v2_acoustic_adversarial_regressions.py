"""Regression tests for source-bound acoustic proof at the prepare API."""
import hashlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_golden_v2_auto_routes import AutoRouteTests
from app.providers.golden_v2_auto_acceptance import evaluate_auto_acceptance


class MalformedEvaluatorRegressionTests(unittest.TestCase):
    def valid_kwargs(self):
        return dict(job_id='v2p_'+'a'*32, source_sha256='a'*64,
                    source_audio_sha_verified=True, source_duration_ms=6000,
                    srt_bytes=b'1\n00:00:00,000 --> 00:00:06,000\nHi\n', txt_bytes=b'Hi\n',
                    reference_txt_bytes=None, original_srt_bytes=None,
                    acoustic_review_items=[], chirp_stage='COMPLETE')

    def test_malformed_items_are_technical_blockers(self):
        for value in ([None], [1], ['oops'], {'bad':1}, [False], False, ''):
            with self.subTest(value=value):
                result = evaluate_auto_acceptance(**(self.valid_kwargs() | {'acoustic_review_items':value}))
                self.assertEqual(result['status'], 'AUTO_BLOCKED_TECHNICAL')
                self.assertIn('invalid_acoustic_review_items', result['technical_blockers'])

    def test_malformed_duration_is_technical_blocker(self):
        for value in (None, '6000', False, -1, 10**400):
            with self.subTest(value=value):
                result = evaluate_auto_acceptance(**(self.valid_kwargs() | {'source_duration_ms':value}))
                self.assertEqual(result['status'], 'AUTO_BLOCKED_TECHNICAL')
                self.assertIn('unverified_source_duration', result['technical_blockers'])


class AcousticPrepareRegressionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AutoRouteTests('test_api_disabled_without_auth_and_does_not_prepare')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.folder = self.fixture.jobdir / 'golden-v2'

    def sidecar(self, *, minimum=1000, gap=None, gap_end=5000):
        srt = (f'1\n00:00:00,000 --> 00:00:01,000\nHello\n\n'
               f'2\n00:00:{gap_end//1000:02d},000 --> 00:00:06,000\nWorld\n').encode()
        (self.folder / 'candidate.srt').write_bytes(srt)
        (self.folder / 'candidate.txt').write_bytes(b'Hello\nWorld\n')
        digest = hashlib.sha256(srt).hexdigest()
        gaps = [gap] if gap is not None else []
        report = {'schema': 'golden-v2-auto-gap-triage-v1', 'source_sha256': self.fixture.source,
                  'srt_sha256': digest, 'duration_ms': 6000, 'gap_min_ms': minimum,
                  'gaps': gaps, 'total_gaps': len(gaps)}
        raw = json.dumps(report, indent=2).encode()
        (self.folder / f'auto-gap-triage-{digest[:20]}.json').write_bytes(raw)
        return raw

    def prepare(self):
        with patch.dict(os.environ, {'COURSE_TRANSCRIPT_V2_AUTO_DELIVERY_API_ENABLED': 'true'}), \
             patch('app.golden_v2_auto_routes.subprocess.run', return_value=SimpleNamespace(stdout='6.000')):
            return self.fixture.client.post(self.fixture.url + '/prepare', headers=self.fixture.headers,
                                            json=self.fixture.body())

    def reports(self):
        return (json.loads(next(self.folder.glob('auto-qa-*.json')).read_bytes()),
                next(self.folder.glob('auto-review-*.md')).read_text())

    def test_probe_conflicting_disposition_blocks_prepare(self):
        self.sidecar(gap={'start_ms':1000, 'end_ms':5000, 'disposition':'NONSPEECH_CONFIRMED',
                          'probes':[{'start_ms':1000, 'end_ms':5000, 'disposition':'NONSPEECH_CONFIRMED',
                                     'acoustic_disposition':'SPEECH_CONFIRMED', 'max_volume_dbfs':-100}]})
        self.assertEqual(self.prepare().status_code, 409)

    def test_scope_discloses_excluded_short_gaps_in_json_and_markdown(self):
        self.sidecar(minimum=60000, gap_end=3000)
        response = self.prepare()
        self.assertEqual(response.status_code, 200, response.text)
        report, md = self.reports()
        self.assertEqual(report['acoustic_triage_gap_min_ms'], 60000)
        self.assertEqual(report['acoustic_triage_excluded_short_gap_count'], 1)
        self.assertEqual(report['subtitle_gap_reporting_min_ms'], 3000)
        self.assertIn('60000', md)
        self.assertIn('3000', md)
        self.assertIn('未納入', md)
        self.assertIn('1', md)

    def test_report_hash_is_exact_sidecar_bytes(self):
        raw = self.sidecar(minimum=60000, gap_end=3000)
        response = self.prepare()
        self.assertEqual(response.status_code, 200, response.text)
        report, _ = self.reports()
        self.assertEqual(report['acoustic_triage_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report['acoustic_triage_sha256_format'], 'sha256 of exact sidecar file bytes')

    def test_malformed_vad_claim_cannot_be_used_as_proof(self):
        self.sidecar(gap={'start_ms':1000, 'end_ms':5000, 'disposition':'NONSPEECH_CONFIRMED',
                          'probes':[{'start_ms':1000, 'end_ms':5000, 'disposition':'NONSPEECH_CONFIRMED',
                                     'max_volume_dbfs':-80,
                                     'vad':{'robust_speech':'true', 'robust_non_speech':True}}]})
        self.assertEqual(self.prepare().status_code, 409)

    def test_duplicate_json_keys_cannot_hide_proof_conflict(self):
        raw = self.sidecar(gap={'start_ms':1000, 'end_ms':5000, 'disposition':'NONSPEECH_CONFIRMED',
                                'probes':[{'start_ms':1000, 'end_ms':5000,
                                           'disposition':'NONSPEECH_CONFIRMED', 'max_volume_dbfs':-80}]})
        path = next(self.folder.glob('auto-gap-triage-*.json'))
        raw = raw.replace(b'"max_volume_dbfs": -80', b'"max_volume_dbfs": 0, "max_volume_dbfs": -80')
        path.write_bytes(raw)
        self.assertEqual(self.prepare().status_code, 409)

    def test_absurd_peak_blocks_prepare_even_with_vad(self):
        self.sidecar(gap={'start_ms':1000, 'end_ms':5000, 'disposition':'NONSPEECH_CONFIRMED',
                          'probes':[{'start_ms':1000, 'end_ms':5000, 'disposition':'NONSPEECH_CONFIRMED',
                                     'max_volume_dbfs':-(10**400),
                                     'vad':{'robust_non_speech':True}}]})
        self.assertEqual(self.prepare().status_code, 409)
