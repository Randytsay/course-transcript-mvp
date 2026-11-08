import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from app.jobs.golden_v2_ledger import V2Conflict
from app.subtitles.golden_v2_raw_candidates import export_raw_candidates


class RawCandidateTests(unittest.TestCase):
    def test_review_only_idempotent_export_and_tamper_rejection(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);raw=root/'golden-v2/chirp-evidence/chunk-0000/provider-raw.json'
            raw.parent.mkdir(parents=True);raw.write_bytes(b'provider evidence')
            sha=hashlib.sha256(raw.read_bytes()).hexdigest()
            rows=[dict(status='DONE',window_id='chunk-0000',result_sha=sha)]
            ledger=SimpleNamespace(status=lambda _:dict(source_sha='a'*64),chirp_windows=lambda _:rows)
            merged=dict(source_sha='a'*64,timing_authority='chirp_3',window_result_shas=[sha],
                words=[dict(word='課程',start_ms=0,end_ms=1000)])
            (root/'golden-v2/chirp-merged-candidate-test.json').write_text(json.dumps(merged))
            args=dict(ledger=ledger,job_id='job',job_dir=root)
            result=export_raw_candidates(**args)
            self.assertFalse(result['golden_pass'])
            self.assertEqual(result['status'],'REVIEW_REQUIRED')
            self.assertEqual(export_raw_candidates(**args),result)
            self.assertIn('00:00:00,000 --> 00:00:01,000',
                          (Path(result['directory'])/'candidate.srt').read_text())
            raw.write_bytes(b'changed')
            with self.assertRaises(V2Conflict):export_raw_candidates(**args)
            rows[0]['status']='SUBMITTED'
            with self.assertRaises(V2Conflict):export_raw_candidates(**args)
