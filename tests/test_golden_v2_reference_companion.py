"""No cloud or paid model calls: reference pairing, proof and QA isolation."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_reference_companion import (
    match_sibling_references, reference_comparison_hint, stage_reference_companions,
)


def ref(name, ident, parent='dir001'):
    raw = ('修行需要觀照自己的起心動念' if name.endswith('.txt') else
           '1\n00:00:00,000 --> 00:00:01,000\n修行需要觀照自己的起心動念\n').encode()
    return {'file_id': ident, 'name': name, 'parent_id': parent,
            'source_path': 'gdrive:課程/' + name, 'version': '7',
            'size_bytes': len(raw), 'md5_checksum': hashlib.md5(raw).hexdigest()}, raw


class ReferenceMatchingTests(unittest.TestCase):
    def test_matches_same_lesson_txt_and_srt_in_any_listing_order(self):
        media = [{'file_id': 'a', 'parent_id': 'dir001', 'name': '20221217 清淨心.mp3'}]
        candidates = [ref('清淨心逐字稿.txt', 'txt-file')[0],
                      ref('20221217 清淨心.srt', 'srt-file')[0]]
        outcome = match_sibling_references(media, candidates)
        self.assertEqual(outcome['a']['status'], 'MATCHED')
        self.assertEqual({v['file_id'] for v in outcome['a']['files']}, {'txt-file', 'srt-file'})
        self.assertEqual(match_sibling_references(media, list(reversed(candidates))), outcome)

    def test_ambiguous_and_wrong_folder_fail_closed(self):
        media = [{'file_id': 'a', 'parent_id': 'dir001', 'name': '20221217 清淨心.mp3'},
                 {'file_id': 'b', 'parent_id': 'dir001', 'name': '20221217 法會.mp3'}]
        same_date = ref('20221217逐字稿.txt', 'shared')[0]
        self.assertEqual(match_sibling_references(media, [same_date])['a']['status'], 'AMBIGUOUS')
        self.assertEqual(match_sibling_references(media, [same_date])['b']['status'], 'AMBIGUOUS')
        ref_elsewhere = ref('清淨心逐字稿.txt', 'elsewhere', 'dir002')[0]
        self.assertEqual(match_sibling_references(media, [ref_elsewhere]), {})
        two_versions = [ref('20221217 清淨心逐字稿.txt', 'one')[0],
                        ref('20221217 清淨心參考.txt', 'two')[0]]
        self.assertEqual(match_sibling_references(media[:1], two_versions)['a']['status'], 'AMBIGUOUS')


class ReferenceEvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.job = Path(tmp.name); self.job_id = 'v2p_'+'a'*32
        self.source_sha = 'b'*64
        self.candidate, self.raw = ref('清淨心逐字稿.txt', 'txt-file')
        self.selection = {'status': 'MATCHED', 'files': [self.candidate]}
        self.words = [
            {'word': '修行', 'start_ms': 0, 'end_ms': 250},
            {'word': '需要', 'start_ms': 250, 'end_ms': 500},
            {'word': '觀照', 'start_ms': 500, 'end_ms': 750},
            {'word': '自己的', 'start_ms': 750, 'end_ms': 1000},
            {'word': '動念', 'start_ms': 1000, 'end_ms': 1300},
        ]

    def fetch(self, candidate, target):
        target.write_bytes(self.raw)
        return {**candidate, 'downloaded_md5': candidate['md5_checksum'],
                'downloaded_sha256': hashlib.sha256(self.raw).hexdigest()}

    def stage(self, fetch=None):
        return stage_reference_companions(job_dir=self.job, job_id=self.job_id,
                    source_sha=self.source_sha, references=self.selection, fetch=fetch or self.fetch)

    def test_immutable_stage_compare_and_qa_never_autopasses(self):
        self.assertEqual(self.stage()['status'], 'MATCHED')
        self.assertEqual(self.stage()['status'], 'MATCHED')
        hint = reference_comparison_hint(job_dir=self.job, job_id=self.job_id,
                                         source_sha=self.source_sha, chirp_words=self.words)
        self.assertEqual(hint['references'][0]['status'], 'REVIEW_ONLY')
        self.assertFalse(hint['auto_replacement_allowed'])
        self.assertFalse(hint['human_listening_attested'])
        self.assertGreaterEqual(hint['references'][0]['disagreements'], 1)
        self.assertTrue(all(not c['auto_replacement_allowed'] for c in hint['references'][0]['candidates']))
        manifest = json.loads((self.job/'golden-v2'/'trusted-reference-manifest.json').read_text())
        artifact = self.job/'golden-v2'/manifest['references'][0]['local_filename']
        artifact.write_text('遭到修改')
        with self.assertRaises(V2Conflict):
            reference_comparison_hint(job_dir=self.job, job_id=self.job,
                                      source_sha=self.source_sha, chirp_words=self.words)

    def test_mismatched_drive_version_or_modified_reference_rejected(self):
        def changed(c, t):
            observed = self.fetch(c, t)
            observed['version'] = '8'
            return observed
        with self.assertRaises(V2Conflict): self.stage(changed)
        self.assertFalse((self.job/'golden-v2'/'trusted-reference-manifest.json').exists())
        with self.assertRaises(V2Conflict):
            self.stage(lambda c, t: {**self.fetch(c, t), 'downloaded_sha256': '0'*64})

    def test_no_reference_no_artifact_no_qa_evidence(self):
        self.assertEqual(stage_reference_companions(job_dir=self.job,
                         job_id=self.job_id, source_sha=self.source_sha,
                         references=None, fetch=self.fetch)['status'], 'NO_REFERENCE')
        self.assertIsNone(reference_comparison_hint(job_dir=self.job,
                          job_id=self.job_id, source_sha=self.source_sha, chirp_words=self.words))
        self.assertEqual(stage_reference_companions(job_dir=self.job,
                         job_id=self.job_id, source_sha=self.source_sha,
                         references={'status': 'AMBIGUOUS', 'files': []},
                         fetch=self.fetch)['status'], 'AMBIGUOUS')
