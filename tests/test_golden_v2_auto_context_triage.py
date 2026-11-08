"""Offline reuse of genuine ledger-bound comparison, no new Gemini call."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_auto_context_triage import read_source_bound_comparison


class ContextTriageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);(self.root/'golden-v2').mkdir()
        self.job='v2p_'+'a'*32;self.source='b'*64
        self.report={'kind':'golden_v2_primary_chirp_comparison','job_id':self.job,
            'source_sha256':self.source,'chirp_timing_authority':True,
            'auto_publish_authorized':False,'comparison_reference_kind':'GEMINI_PRIMARY',
            'pending_review_count':1,'candidates':[{'kind':'TEXT_DISAGREEMENT',
             'comparison_status':'REVIEW_REQUIRED','source_time_start':6000,
             'source_time_end':8000,'chirp_normalized_excerpt':'菩提',
             'gemini_normalized_excerpt':'菩薩'}]}
        raw=json.dumps(self.report,ensure_ascii=False).encode()
        self.digest=hashlib.sha256(raw).hexdigest()
        (self.root/'golden-v2'/'engine-comparison.json').write_bytes(raw)
        self.stages=[{'name':'compare','state':'COMPLETE','evidence_sha':self.digest}]

    def read(self):
        return read_source_bound_comparison(job_dir=self.root,job_id=self.job,
                           source_sha256=self.source,stages=self.stages)

    def test_valid_source_bound_comparison_is_advisory(self):
        result=self.read()
        self.assertEqual(result['comparison_reference_kind'],'GEMINI_PRIMARY')
        self.assertEqual(len(result['semantic_review_items']),1)
        self.assertEqual(result['semantic_review_items'][0]['source_time_start_ms'],6000)
        self.assertFalse(result['semantic_correctness_certified'])
        self.assertFalse(result['model_invoked_now'])

    def test_missing_comparison_is_not_run_not_fake_pass(self):
        self.assertIsNone(read_source_bound_comparison(job_dir=self.root,
            job_id=self.job,source_sha256=self.source,stages=[]))

    def test_wrong_source_rejected(self):
        with self.assertRaises(V2Conflict):
            read_source_bound_comparison(job_dir=self.root,
                job_id=self.job,source_sha256='c'*64,stages=self.stages)

    def test_tampered_evidence_or_wrong_digest_rejected(self):
        path=self.root/'golden-v2'/'engine-comparison.json'
        path.write_text('{}')
        with self.assertRaises(V2Conflict):self.read()

    def test_no_gemini_in_chirp_first_comparison(self):
        path=self.root/'golden-v2'/'engine-comparison.json'
        self.report['comparison_reference_kind']='HUMAN_CORRECTED_SRT'
        raw=json.dumps(self.report,ensure_ascii=False).encode()
        path.write_bytes(raw);self.stages[0]['evidence_sha']=hashlib.sha256(raw).hexdigest()
        result=self.read()
        self.assertEqual(result['comparison_reference_kind'],'HUMAN_CORRECTED_SRT')
        self.assertFalse(result['semantic_correctness_certified'])

if __name__=='__main__':unittest.main()
