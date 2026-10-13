import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from app.pipeline.golden_v2_runtime_services import BudgetedModels, signed_grant, verify_job_grant
from app.pipeline.golden_v2_quote_intake import wire
from app.providers.golden_v2_audio_staging import file_sha256
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict


class RuntimeServicesTests(unittest.TestCase):
    def test_actual_token_count_prevents_paid_request_and_wrong_model(self):
        models=MagicMock(); wrapper=BudgetedModels(models)
        models.count_tokens.return_value=SimpleNamespace(total_tokens=200001)
        with self.assertRaises(V2Conflict): wrapper.generate_content(model='gemini-3.8-flash',contents=[],config={})
        models.generate_content.assert_not_called()
        with self.assertRaises(V2Conflict): wrapper.generate_content(model='gemini-2.5-flash',contents=[],config={})
        models.count_tokens.return_value=SimpleNamespace(total_tokens=100)
        wrapper.generate_content(model='gemini-3.8-flash',contents=['audio'],config={})
        self.assertEqual(models.generate_content.call_args.kwargs['config']['max_output_tokens'],65536)

    def test_grants_require_secret_and_detect_forgery_source_quote_and_cap(self):
        with patch.dict(os.environ,{'COURSE_TRANSCRIPT_V2_GRANT_SECRET':''}):
            with self.assertRaises(V2Conflict): signed_grant({})
        with TemporaryDirectory() as temporary, patch.dict(os.environ,{'COURSE_TRANSCRIPT_V2_GRANT_SECRET':'s'*48}):
            root=Path(temporary); ledger=GoldenV2Ledger(root/'ledger.sqlite')
            estimates=dict(gemini_primary=40,chirp_independent=2,compare=0,third_optional=0,adjudication=0,golden_qa=0)
            job_id=ledger.create_from_preflight(source_sha='a'*64,mode='FLASH_CHIRP',estimated_stage_cents=estimates,registration_sha256='b'*64)
            directory=root/'jobs'/job_id; directory.mkdir(parents=True)
            quote_path=directory/'trusted-quote.json'
            quote_path.write_text(wire(dict(source_sha256='a'*64,estimated_cents=42,mode='FLASH_CHIRP')))
            grant=dict(job_id=job_id,source_sha256='a'*64,quote_sha256=file_sha256(quote_path),approved_cents=100,actor='owner')
            grant_path=directory/'owner-paid-grant.json'; grant_path.write_text(wire(signed_grant(grant)))
            state=ledger.status(job_id); ledger.approve(job_id,revision=state['revision'],approved_cents=100,reference='human-approval',actor='owner')
            snapshot=dict(job_id=job_id,source_sha256='a'*64,aggregate_cap_cents=1000)
            with patch('app.pipeline.golden_v2_runtime_services.assert_current'):
                self.assertEqual(verify_job_grant(root=root,snapshot=snapshot)[1],grant)
                for key,value in [('source_sha256','c'*64),('aggregate_cap_cents',99)]:
                    with self.assertRaises(V2Conflict): verify_job_grant(root=root,snapshot={**snapshot,key:value})
                forged={**signed_grant(grant),'signature':'0'*64}; grant_path.write_text(wire(forged))
                with self.assertRaises(V2Conflict): verify_job_grant(root=root,snapshot=snapshot)

    def test_paid_route_is_disabled_even_for_authenticated_write_token(self):
        from fastapi.testclient import TestClient
        from app.golden_v2_chat_api import app
        with TemporaryDirectory() as temporary:
            db=Path(temporary)/'ledger.sqlite'; GoldenV2Ledger(db)
            with patch.dict(os.environ,{'COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED':'true',
                 'COURSE_TRANSCRIPT_V2_WRITE_TOKEN':'w'*40,'COURSE_TRANSCRIPT_V2_OWNER_ACTOR':'owner',
                 'COURSE_TRANSCRIPT_V2_LEDGER_PATH':str(db),'COURSE_TRANSCRIPT_V2_PAID_API_ENABLED':'false'}):
                response=TestClient(app).post('/api/v1/golden-v2/jobs/v2p_'+'a'*32+'/authorize-paid',
                    json=dict(revision=0,quote_sha256='b'*64,source_sha256='c'*64,approved_cents=100,reference='owner',confirm=True),
                    headers={'Authorization':'Bearer '+'w'*40})
            self.assertEqual(response.status_code,404)
