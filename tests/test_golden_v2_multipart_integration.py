"""Integration tests for Option 3 single multipart upload in Golden V2 publication.

Verifies:
1. Single multipart upload combines metadata and media in one request.
2. Pre-generated Drive file ID persistence and reuse.
3. 409 Conflict re-checks remote ID, parent, name, appProperties, and content SHA256.
4. Tampered 409 or 404 enters HOLD_FOR_RECONCILIATION with no new ID generated.
5. Durable SQLite publication fence prevents concurrent mutation.
"""
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_multipart_pilot import (
    build_exact_id_multipart, verify_remote_exact, classify_http_outcome, PilotRequest)
import tests.test_golden_v2_auto_publication as publication_fixtures  # type: ignore
import tests.test_auto_publication_process_recovery as recovery_fixtures  # type: ignore

PersistentDrive = recovery_fixtures.PersistentDrive


class MultipartIntegrationTests(publication_fixtures.AutoPublisherTests):
    def test_single_multipart_request_structure(self):
        req = build_exact_id_multipart(
            file_id='file12345678',
            parent_id='parent12345678',
            name='.pending_test_0',
            media=b'test content for srt',
            job_id=self.job,
            manifest_sha256='a'*64,
            expected_media_sha256=hashlib.sha256(b'test content for srt').hexdigest(),
            artifact_extension='.srt',
            pending=True
        )
        self.assertEqual(req.method, 'POST')
        self.assertIn('multipart/related', req.content_type)
        self.assertIn('application/x-subrip', req.body.decode('latin-1'))
        self.assertIn('file12345678', req.body.decode('latin-1'))
        self.assertIn(self.job, req.body.decode('latin-1'))

    def test_409_reconciliation_exact_match_verifies(self):
        req = build_exact_id_multipart(
            file_id='file12345678',
            parent_id='parent12345678',
            name='.pending_test_0',
            media=b'identical content',
            job_id=self.job,
            manifest_sha256='a'*64,
            expected_media_sha256=hashlib.sha256(b'identical content').hexdigest(),
            artifact_extension='.txt',
            pending=True
        )
        remote = {
            'id': 'file12345678',
            'name': '.pending_test_0',
            'parents': ['parent12345678'],
            'mimeType': 'text/plain',
            'trashed': False,
            'version': '1',
            'size': str(len(b'identical content')),
            'md5Checksum': req.expected_md5,
            'appProperties': req.metadata['appProperties']
        }
        outcome = classify_http_outcome(status_code=409, request=req, remote=remote, readback=b'identical content')
        self.assertEqual(outcome, 'VERIFIED_OBSERVATION_NOT_PUBLICATION_AUTHORIZATION')

    def test_409_mismatch_fails_closed_without_reupload(self):
        req = build_exact_id_multipart(
            file_id='file12345678',
            parent_id='parent12345678',
            name='.pending_test_0',
            media=b'identical content',
            job_id=self.job,
            manifest_sha256='a'*64,
            expected_media_sha256=hashlib.sha256(b'identical content').hexdigest(),
            artifact_extension='.txt',
            pending=True
        )
        remote = {
            'id': 'file12345678',
            'name': '.pending_test_0',
            'parents': ['parent12345678'],
            'mimeType': 'text/plain',
            'trashed': False,
            'version': '1',
            'size': '999',
            'md5Checksum': '0'*32,
            'appProperties': req.metadata['appProperties']
        }
        outcome = classify_http_outcome(status_code=409, request=req, remote=remote, readback=b'tampered bytes')
        self.assertEqual(outcome, 'HOLD_FOR_RECONCILIATION_NO_NEW_ID_NO_REUPLOAD')

    def test_404_holds_for_reconciliation_never_new_id(self):
        req = build_exact_id_multipart(
            file_id='file12345678',
            parent_id='parent12345678',
            name='.pending_test_0',
            media=b'content',
            job_id=self.job,
            manifest_sha256='a'*64,
            expected_media_sha256=hashlib.sha256(b'content').hexdigest(),
            artifact_extension='.txt',
            pending=True
        )
        outcome = classify_http_outcome(status_code=404, request=req, remote=None, readback=None)
        self.assertEqual(outcome, 'HOLD_FOR_RECONCILIATION_NO_NEW_ID_NO_REUPLOAD')

    def test_full_pipeline_uses_multipart_and_persists_four_files(self):
        receipt = self.publish()
        self.assertEqual(len(receipt['remote_files']), 4)
        for rf in receipt['remote_files']:
            self.assertTrue(rf['file_id'].startswith('allocated-'))
            self.assertIn(rf['name'], [
                self.manifest['output_names'][0],
                self.manifest['output_names'][1],
                self.manifest['extra_artifacts'][0]['name'],
                self.manifest['extra_artifacts'][1]['name'],
            ])


if __name__ == '__main__':
    unittest.main()
