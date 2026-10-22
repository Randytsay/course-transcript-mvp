"""Read-only HTTP review of a held, Ledger-selected Chirp candidate."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.golden_v2_review_routes import router
from app.jobs.golden_v2_ledger import GoldenV2Ledger


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode()


def write_record(path, record, *, indent=None):
    data = (wire(record) if indent is None else
            json.dumps(record, ensure_ascii=False, indent=indent).encode())
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class CurrentChirpReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root/'ledger.sqlite'
        ledger = GoldenV2Ledger(self.database)
        self.source_sha = 'a'*64
        self.job_id = ledger.create_from_preflight(
            source_sha=self.source_sha, mode='FLASH_CHIRP',
            estimated_stage_cents=dict(gemini_primary=1, chirp_independent=2,
                compare=0, third_optional=0, adjudication=0, golden_qa=0),
            registration_sha256='b'*64)
        self.directory = self.root/'jobs'/self.job_id/'golden-v2'
        self.directory.mkdir(parents=True)
        self.candidate = dict(schema='golden-v2-chirp-merged-v1',
            job_id=self.job_id, source_sha=self.source_sha,
            duration_ms=3000, timing_authority='chirp_3',
            words=[dict(word='好', start_ms=100, end_ms=300)])
        self.candidate_sha = hashlib.sha256(wire(self.candidate)).hexdigest()
        self.candidate_path = self.directory/f'chirp-merged-candidate-{self.candidate_sha[:20]}.json'
        write_record(self.candidate_path, self.candidate)
        # Direct writes are ONLY fixture construction in a disposable DB.
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE v2_jobs SET status='NEEDS_REVIEW' WHERE id=?", (self.job_id,))
            db.execute("UPDATE v2_stages SET state='COMPLETE' WHERE job_id=? AND name='gemini_primary'", (self.job_id,))
            db.execute("UPDATE v2_stages SET state='REVIEW_REQUIRED', evidence_sha=? WHERE job_id=? AND name='chirp_independent'", (self.candidate_sha, self.job_id))
        self.app = FastAPI()
        self.app.include_router(router)

    def get_review(self):
        with patch.dict(os.environ, {
            'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED': 'true',
            'COURSE_TRANSCRIPT_V2_STATUS_TOKEN': 't'*40,
            'COURSE_TRANSCRIPT_V2_LEDGER_PATH': str(self.database),
        }):
            return TestClient(self.app).get(
                f'/api/v1/golden-v2/jobs/{self.job_id}/review',
                headers={'Authorization': 'Bearer '+'t'*40})

    def acoustic_record(self, *, merged_sha=None, **changes):
        return {**dict(schema='golden-v2-acoustic-qa-v1',
            source_sha256=self.source_sha,
            merged_sha256=merged_sha or self.candidate_sha,
            status='REVIEW', blocker_count=12, uncertain_count=11,
            unexamined_gap_count=0, gaps_examined=[]), **changes}

    def add_acoustic(self, record):
        digest = hashlib.sha256(wire(record)).hexdigest()
        path = self.directory/f'chirp-acoustic-qa-{digest[:20]}.json'
        write_record(path, record)
        return path, digest

    def test_current_held_candidate_replaces_legacy_counts_without_certifying_qa(self):
        record = self.acoustic_record()
        _, acoustic_sha = self.add_acoustic(record)
        legacy = {**record, 'merged_sha256': 'c'*64,
                  'blocker_count': 64, 'uncertain_count': 64,
                  'unexamined_gap_count': 59}
        write_record(self.directory/'acoustic-qa.json', legacy)
        before = self.database.read_bytes()
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        selected = result['reports']['acoustic-qa.json']
        self.assertEqual(selected['report']['blocker_count'], 12)
        self.assertEqual(selected['sha256'], acoustic_sha)
        self.assertTrue(selected['bound_current_chirp'])
        self.assertTrue(result['reports']['historical-acoustic-qa.json']['legacy_unbound'])
        self.assertFalse(result['current_chirp_acoustic_review']['golden_pass'])
        self.assertFalse(result['current_chirp_acoustic_review']['human_listening_attested'])
        self.assertTrue(result['human_listening_required'])
        self.assertEqual(self.database.read_bytes(), before)

    def replace_candidate(self, record, *, indent=None):
        self.candidate_path.unlink()
        canonical_sha = hashlib.sha256(wire(record)).hexdigest()
        data = (wire(record) if indent is None else
                json.dumps(record, ensure_ascii=False, indent=indent).encode())
        digest = hashlib.sha256(data).hexdigest()
        self.candidate_path = self.directory/f'chirp-merged-candidate-{digest[:20]}.json'
        self.candidate_path.write_bytes(data)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE v2_stages SET evidence_sha=? WHERE job_id=? AND name='chirp_independent'", (digest, self.job_id))
        return digest, canonical_sha

    def test_indented_candidate_binds_byte_sha_and_distinct_canonical_sha(self):
        byte_sha, canonical_sha = self.replace_candidate(self.candidate, indent=2)
        self.assertNotEqual(byte_sha, canonical_sha)
        self.add_acoustic(self.acoustic_record(merged_sha=canonical_sha))
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()['current_chirp_acoustic_review']
        self.assertEqual(current['status'], 'BOUND_CURRENT')
        self.assertEqual(current['chirp_evidence_sha256'], byte_sha)
        self.assertEqual(current['merged_sha256'], canonical_sha)

    def test_full_sha_versioned_candidate_is_supported(self):
        self.candidate_path.rename(self.directory/f'chirp-merged-{self.candidate_sha}.json')
        self.add_acoustic(self.acoustic_record())
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['current_chirp_acoustic_review']['status'], 'BOUND_CURRENT')

    def test_historical_canonical_and_newer_unrelated_report_are_not_selected(self):
        write_record(self.directory/'chirp-merged.json', {**self.candidate, 'words': []})
        _, current_sha = self.add_acoustic(self.acoustic_record())
        historical, _ = self.add_acoustic(self.acoustic_record(merged_sha='c'*64, blocker_count=99))
        os.utime(historical, (2000000000, 2000000000))
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['reports']['acoustic-qa.json']['sha256'], current_sha)

    def test_changed_candidate_fails_closed(self):
        self.add_acoustic(self.acoustic_record())
        self.candidate_path.write_bytes(b'{}')
        response = self.get_review()
        self.assertEqual(response.status_code, 409, response.text)

    def test_candidate_source_mismatch_fails_closed(self):
        self.replace_candidate({**self.candidate, 'source_sha': 'd'*64})
        self.assertEqual(self.get_review().status_code, 409)

    def test_candidate_job_mismatch_fails_closed(self):
        self.replace_candidate({**self.candidate, 'job_id': 'v2p_'+'e'*32})
        self.assertEqual(self.get_review().status_code, 409)

    def test_report_source_mismatch_fails_closed(self):
        self.add_acoustic(self.acoustic_record(source_sha256='d'*64))
        self.assertEqual(self.get_review().status_code, 409)

    def test_report_filename_does_not_match_content_fails_closed(self):
        path, _ = self.add_acoustic(self.acoustic_record())
        path.rename(self.directory/('chirp-acoustic-qa-'+'0'*20+'.json'))
        self.assertEqual(self.get_review().status_code, 409)

    def test_conflicting_reports_are_ambiguous_not_latest_wins(self):
        self.add_acoustic(self.acoustic_record())
        self.add_acoustic(self.acoustic_record(blocker_count=1))
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['current_chirp_acoustic_review']['status'], 'AMBIGUOUS')
        self.assertNotIn('acoustic-qa.json', result['reports'])
        self.assertFalse(result['current_chirp_acoustic_review']['golden_pass'])

    def test_identical_report_copies_deduplicate_by_full_hash(self):
        path, digest = self.add_acoustic(self.acoustic_record())
        (self.directory/f'chirp-acoustic-qa-{digest}.json').write_bytes(path.read_bytes())
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['current_chirp_acoustic_review']['status'], 'BOUND_CURRENT')

    def test_candidate_symlink_fails_closed(self):
        other = self.root/'outside.json'
        other.write_bytes(self.candidate_path.read_bytes())
        self.candidate_path.unlink()
        self.candidate_path.symlink_to(other)
        self.assertEqual(self.get_review().status_code, 409)

    def test_report_symlink_fails_closed(self):
        path, _ = self.add_acoustic(self.acoustic_record())
        other = self.root/'outside-report.json'
        path.rename(other)
        path.symlink_to(other)
        self.assertEqual(self.get_review().status_code, 409)

    def test_oversize_report_fails_closed(self):
        self.add_acoustic(self.acoustic_record(review_notes='x'*(2*1024**2)))
        self.assertEqual(self.get_review().status_code, 409)

    def test_legacy_only_report_remains_explicitly_unbound(self):
        write_record(self.directory/'acoustic-qa.json', self.acoustic_record(merged_sha='c'*64))
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['current_chirp_acoustic_review']['status'], 'UNAVAILABLE')
        self.assertTrue(result['reports']['acoustic-qa.json']['legacy_unbound'])

    def test_pending_chirp_has_no_current_review_selection(self):
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE v2_stages SET state='PENDING',evidence_sha=NULL WHERE job_id=? AND name='chirp_independent'", (self.job_id,))
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['current_chirp_acoustic_review']['status'], 'UNAVAILABLE')

    def test_qa_attested_acoustic_chain_keeps_precedence(self):
        self.add_acoustic(self.acoustic_record())
        legacy_sha = write_record(self.directory/'acoustic-qa.json', self.acoustic_record(merged_sha='c'*64))
        qa_sha = write_record(self.directory/'golden-qa.json', dict(acoustic_sha256=legacy_sha))
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE v2_stages SET state='REVIEW_REQUIRED',evidence_sha=? WHERE job_id=? AND name='golden_qa'", (qa_sha, self.job_id))
        response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        selected = response.json()['reports']['acoustic-qa.json']
        self.assertEqual(selected['sha256'], legacy_sha)
        self.assertNotIn('bound_current_chirp', selected)

    def test_unknown_job_is_404_without_initializing_or_mutating_database(self):
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM v2_stages WHERE job_id=?', (self.job_id,))
            db.execute('DELETE FROM v2_jobs WHERE id=?', (self.job_id,))
        before = self.database.read_bytes()
        response = self.get_review()
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(before, self.database.read_bytes())

    def test_unauthorized_read_is_rejected(self):
        with patch.dict(os.environ, {
            'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED': 'true',
            'COURSE_TRANSCRIPT_V2_STATUS_TOKEN': 't'*40,
            'COURSE_TRANSCRIPT_V2_LEDGER_PATH': str(self.database),
        }):
            response = TestClient(self.app).get(
                f'/api/v1/golden-v2/jobs/{self.job_id}/review',
                headers={'Authorization': 'Bearer '+'x'*40})
        self.assertEqual(response.status_code, 401)

    def test_review_get_never_opens_a_writable_ledger(self):
        with patch.object(GoldenV2Ledger, 'open_existing',
                          wraps=GoldenV2Ledger.open_existing) as opened:
            response = self.get_review()
        self.assertEqual(response.status_code, 200, response.text)
        opened.assert_not_called()
