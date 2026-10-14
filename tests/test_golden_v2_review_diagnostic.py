"""Offline, provider-free reasons for a Golden V2 primary-stage review hold."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.golden_v2_review_routes import _primary_hold_diagnostic, router
from app.pipeline.golden_v2_primary_stage import _primary_audit_path
from app.providers.gemini_primary_audio import PROMPT_VERSION


def write_json(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProviderReviewDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job_dir = Path(self.temp.name)
        self.job_id = 'v2p_' + 'a' * 32
        self.source_sha = 'b' * 64
        self.window = {
            'index': 0, 'start_seconds': 0.0, 'end_seconds': 240.0,
            'gcs_uri': 'gs://private/clip.flac', 'mime_type': 'audio/flac',
            'clip_sha256': 'c' * 64,
        }
        self.identity = {
            'model': 'gemini-3.8-flash', 'prompt_version': PROMPT_VERSION,
            'source_sha256': self.source_sha,
            'context_sha256': 'd' * 64,
            'window': {k: self.window[k] for k in (
                'index', 'start_seconds', 'end_seconds', 'gcs_uri', 'mime_type')},
        }

    def make_report(self, *, status='REQUIRES_REVIEW', text='候選文字',
                    finish='MAX_TOKENS'):
        self.raw_path = _primary_audit_path(self.job_dir, self.identity)
        self.raw = {
            'identity': self.identity, 'status': status, 'text': text,
            'finish_reason': finish, 'usage_metadata': {'total_token_count': 7},
            'human_review_required': status != 'COMPLETE',
        }
        sha = write_json(self.raw_path, self.raw)
        self.report = {
            'schema': 'golden-v2-primary-stage-v1',
            'job_id': self.job_id, 'source_sha256': self.source_sha,
            'mode': 'gemini_3_8_audio_first',
            'ready_for_chirp': False, 'golden_approved': False,
            'windows': [self.window],
            'window_evidence': [{
                'identity': self.identity, 'raw_audit_sha256': sha,
                'finish_reason': finish, 'usage_metadata': {'total_token_count': 7},
            }],
        }
        self.report_path = self.job_dir/'golden-v2'/'primary-stage.json'
        self.report_sha = write_json(self.report_path, self.report)

    def diagnose(self):
        return _primary_hold_diagnostic(self.job_dir, self.job_id,
                                        self.source_sha, self.report_sha)

    def test_non_stop_reason_is_audit_bound_without_exposing_text(self):
        self.make_report()
        result = self.diagnose()
        self.assertEqual(result, {
            'diagnostic_code': 'GEMINI_NON_STOP_FINISH', 'window_index': 0,
        })
        self.assertNotIn('候選文字', str(result))

    def test_empty_response_is_identified_without_replaying(self):
        self.make_report(text='', finish='STOP')
        self.assertEqual(self.diagnose()['diagnostic_code'], 'GEMINI_EMPTY_RESPONSE')

    def test_valid_completed_raw_is_not_falsely_labeled_incomplete(self):
        self.make_report(status='COMPLETE', finish='STOP')
        self.assertEqual(self.diagnose()['diagnostic_code'],
                         'PRIMARY_OVERLAP_EVIDENCE_CONFLICT')

    def test_raw_tampering_takes_priority_over_response_classification(self):
        self.make_report()
        self.raw_path.write_text('{}', encoding='utf-8')
        self.assertEqual(self.diagnose()['diagnostic_code'],
                         'PRIMARY_RAW_AUDIT_MISSING_OR_CHANGED')

    def test_partial_windows_preserve_exact_failed_window(self):
        self.make_report()
        # A later window can be held before every staged window was returned.
        self.report['windows'].append({
            **self.window, 'index': 1, 'start_seconds': 230.0,
            'end_seconds': 470.0,
        })
        self.report_sha = write_json(self.report_path, self.report)
        self.assertEqual(self.diagnose(), {
            'diagnostic_code': 'GEMINI_NON_STOP_FINISH', 'window_index': 0,
        })

    def test_prompt_mismatch_never_classified_as_known_response(self):
        self.make_report()
        self.report['window_evidence'][0]['identity']['prompt_version'] = 'other'
        self.report_sha = write_json(self.report_path, self.report)
        self.assertEqual(self.diagnose()['diagnostic_code'],
                         'PRIMARY_WINDOW_BINDING_CONFLICT')

    def test_unbound_source_or_missing_report_cannot_justify_retry(self):
        self.make_report()
        self.assertEqual(_primary_hold_diagnostic(
            self.job_dir, self.job_id, 'e' * 64, self.report_sha)['diagnostic_code'],
            'PRIMARY_REPORT_BINDING_CONFLICT')
        self.report_path.unlink()
        self.assertEqual(self.diagnose()['diagnostic_code'],
                         'PRIMARY_REPORT_MISSING_OR_CHANGED')

    def test_authenticated_review_endpoint_reports_code_without_raw_text(self):
        self.make_report()
        app = FastAPI()
        app.include_router(router)
        state = {
            'revision': 6, 'source_sha': self.source_sha,
            'stages': [{'name': 'gemini_primary', 'state': 'REVIEW_REQUIRED',
                        'evidence_sha': self.report_sha}],
        }
        with patch('app.golden_v2_review_routes._authorized_database',
                   return_value=self.job_dir/'ledger.db'), \
                patch('app.golden_v2_review_routes.job_directory',
                      return_value=self.job_dir), \
                patch('app.golden_v2_review_routes.GoldenV2Ledger.open_existing',
                      return_value=SimpleNamespace(status=lambda _: state)), \
                patch('app.golden_v2_review_routes.inspect_pending_overlaps',
                      side_effect=ValueError('private audio transcript')):
            response = TestClient(app).get(
                f'/api/v1/golden-v2/jobs/{self.job_id}/review',
                headers={'Authorization': 'Bearer test'},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['kind'], 'PROVIDER_RESPONSE_REVIEW')
        self.assertEqual(payload['diagnostic_code'], 'GEMINI_NON_STOP_FINISH')
        self.assertTrue(payload['human_listening_required'])
        self.assertNotIn('private audio transcript', response.text)
        self.assertNotIn('候選文字', response.text)


if __name__ == '__main__':
    unittest.main()
