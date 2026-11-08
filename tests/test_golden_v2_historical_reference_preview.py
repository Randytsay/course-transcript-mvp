"""Historical evidence preview never certifies audio, touches Prod or Drive."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_historical_reference_preview import preview_historical_references


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class HistoricalPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.refs = self.root / 'references'; self.refs.mkdir()
        self.plan_root = self.root / 'planning'; self.plan_root.mkdir()
        self.job = 'v2p_' + 'a'*32
        self.source = 'b'*64
        self.manifest = {
            'schema': 'golden-v2-20221217-acoustic-human-review-v1',
            'job_id': self.job, 'source_sha256': self.source,
            'acoustic_report_sha256': 'c'*64,
            'human_listened': False,
            'review_items': [
                {'index': 1, 'gap_start_ms': 2000, 'gap_end_ms': 3000,
                 'disposition': 'SPEECH_CONFIRMED', 'human_listened': False},
                {'index': 2, 'gap_start_ms': 4000, 'gap_end_ms': 5000,
                 'disposition': 'REVIEW', 'human_listened': False},
            ]
        }
        self.encoded = json.dumps(self.manifest,sort_keys=True).encode()
        base = b'1\n00:00:01,000 --> 00:00:03,000\nhello world\n'
        reviewed = b'1\n00:00:01,000 --> 00:00:03,000\nhello earth\n'
        txt = 'hello world and other lecture content'.encode()
        self.plan = {'schema': 'golden-v2-historical-reference-plan-v1',
                     'job_id': self.job, 'source_sha256': self.source,
                     'references': []}
        for role, extension, raw in [
                ('BASE_SRT', 'srt', base), ('REVIEWED_SRT', 'srt', reviewed),
                ('REFERENCE_TXT', 'txt', txt)]:
            path = self.refs / (role + '.' + extension)
            path.write_bytes(raw)
            self.plan['references'].append({
                'role': role, 'local_path': str(path), 'sha256': sha(raw),
                'drive_file_id': 'drive-' + role + '12345',
                'drive_version': '8', 'drive_parent_id': 'folder-12345678'})

    def run_preview(self, **override):
        params = dict(plan=self.plan, gap_manifest=self.manifest,
                      expected_gap_manifest_sha256=sha(self.encoded),
                      gap_manifest_bytes=self.encoded, expected_job_id=self.job,
                      expected_source_sha256=self.source,
                      plan_directory=self.plan_root)
        return preview_historical_references(**(params | override))

    def test_has_text_not_acoustic_pass_and_flags_variant_disagreement(self):
        report = self.run_preview()
        self.assertEqual(report['counts'], {
            'both_srt_text_present': 1, 'any_srt_timeline_empty': 1,
            'base_reviewed_text_disagreement': 1, 'human_listening_pending': 2})
        self.assertFalse(report['golden_qa_pass'])
        self.assertFalse(report['human_listening_attested'])
        self.assertFalse(report['production_delivery_allowed'])
        self.assertFalse(report['provider_calls_started'])
        self.assertFalse(report['drive_writes_started'])
        self.assertTrue(report['gaps'][0]['text_variant_disagreement'])
        self.assertTrue(report['gaps'][0]['reference_txt_available'])
        self.assertFalse(report['gaps'][0]['reference_txt_anchor_hints']['BASE_SRT']['acoustic_proof'])
        self.assertEqual(report['gaps'][1]['srt']['BASE_SRT']['overlapping_cue_count'], 0)

    def test_rejects_wrong_source_or_unverified_gap_evidence(self):
        with self.assertRaises(V2Conflict):
            self.run_preview(expected_source_sha256='d'*64)
        with self.assertRaises(V2Conflict):
            self.run_preview(expected_gap_manifest_sha256='d'*64)
        unsafe = {**self.manifest, 'human_listened': True}
        with self.assertRaises(V2Conflict):
            self.run_preview(gap_manifest=unsafe,
                             gap_manifest_bytes=json.dumps(unsafe).encode(),
                             expected_gap_manifest_sha256=sha(json.dumps(unsafe).encode()))

    def test_cannot_use_unpinned_reference_or_different_parent(self):
        (self.refs/'BASE_SRT.srt').write_text('tampered')
        with self.assertRaises(V2Conflict): self.run_preview()
        self.plan['references'][0]['sha256'] = sha(b'tampered')
        with self.assertRaises(V2Conflict): self.run_preview()

    def test_shared_role_and_parent_rejected(self):
        self.plan['references'][1]['role'] = 'BASE_SRT'
        with self.assertRaises(V2Conflict): self.run_preview()
        self.plan['references'][1]['role'] = 'REVIEWED_SRT'
        self.plan['references'][1]['drive_parent_id'] = 'otherfolder12345678'
        with self.assertRaises(V2Conflict): self.run_preview()

    def test_reference_copy_must_be_outside_plan_and_is_not_mutated(self):
        before = {p.name: p.read_bytes() for p in self.refs.iterdir()}
        self.run_preview()
        self.assertEqual({p.name: p.read_bytes() for p in self.refs.iterdir()}, before)
        self.plan['references'][2]['local_path'] = str(self.plan_root/'owned.txt')
        (self.plan_root/'owned.txt').write_text('other')
        with self.assertRaises(V2Conflict): self.run_preview()


if __name__ == '__main__':
    unittest.main()
