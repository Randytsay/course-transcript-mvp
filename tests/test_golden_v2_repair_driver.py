"""Driver regressions: no second claim, no empty verification success."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

class DriverClaimTests(unittest.TestCase):
    def load_driver(self):
        path=Path(__file__).resolve().parents[1]/'scripts/golden_v2_lossless_repair_drive.py'
        spec=importlib.util.spec_from_file_location('repair_driver_test',path)
        m=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'COURSE_TRANSCRIPT_V2_ARTIFACT_ROOT':'/nonexistent-audit-root','COURSE_TRANSCRIPT_V2_LEDGER_PATH':'/nonexistent-ledger','GCS_BUCKET':'test-bucket'}):
            spec.loader.exec_module(m)
        from tempfile import TemporaryDirectory
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        setattr(m,'ROOT',Path(tmp.name))
        (Path(tmp.name)/'jobs'/('v2p_'+'a'*32)).mkdir(parents=True)
        return m

    def ledger(self):
        ledger=Mock()
        ledger.assert_stage_claim=Mock()
        stages=[{'name':n,'state':s,'evidence_sha':h*64} for n,s,h in [('gemini_primary','COMPLETE','a'),('chirp_independent','COMPLETE','b'),('compare','COMPLETE','c'),('third_optional','SKIPPED','d'),('adjudication','COMPLETE','e'),('golden_qa','REVIEW_REQUIRED','f')]]
        ledger.status.return_value={'status':'NEEDS_REVIEW','revision':11,'source_sha':'1'*64,'stages':stages}
        ledger.resume_chirp_review_for_repair.return_value={'job_id':'v2p_'+'a'*32,'stage':'chirp_independent','claim':'private-test-capability','source_sha':'1'*64,'estimate_cents':70}
        ledger.claim_next.side_effect=AssertionError('reopen ALREADY issued a claim')
        return ledger

    def test_resume_claim_is_not_claimed_twice(self):
        m=self.load_driver();ledger=self.ledger()
        with patch.object(m,'finish_approved_chirp',return_value={'words':[{'word':'甲'}],'acoustic_gate':'PASS'}),patch.object(m,'log'):
            m.deliver(ledger,'v2p_'+'a'*32,'test')
        ledger.claim_next.assert_not_called()

    def test_cli_refuses_unreviewed_vad_only_dispatch(self):
        m=self.load_driver()
        with patch.object(m.GoldenV2Ledger,'open_existing',return_value=self.ledger()) as opened,patch.object(m,'deliver'),patch.object(m,'log'):
            result=m.main()
        self.assertEqual(result,2)
        opened.assert_not_called()

    def test_missing_raw_windows_cannot_be_verified(self):
        m=self.load_driver();ledger=self.ledger()
        def resume(*args,**kw):
            self.assertFalse(kw['evidence_verifier'](job_id='v2p_'+'a'*32,source_sha='1'*64,old_chirp_sha='b'*64,old_qa_sha='f'*64,windows=[]))
            raise RuntimeError('test stopped before mutation')
        ledger.resume_chirp_review_for_repair.side_effect=resume
        with patch.object(m,'log'),self.assertRaisesRegex(RuntimeError,'test stopped'):
            m.deliver(ledger,'v2p_'+'a'*32,'test')

if __name__=='__main__':unittest.main()
