"""Owner routes for standing auto approval, never accessible anonymously."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.golden_v2_chat_api import app
from app.jobs.golden_v2_ledger import GoldenV2Ledger
from tests.test_golden_v2_auto_six import AutoSixTests


class AutoSixAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        GoldenV2Ledger(self.root/'ledger.sqlite')
        self.patch=patch.dict('os.environ',{
          'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED':'true',
          'COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED':'true',
          'COURSE_TRANSCRIPT_V2_PAID_API_ENABLED':'true',
          'COURSE_TRANSCRIPT_V2_STATUS_TOKEN':'s'*40,
          'COURSE_TRANSCRIPT_V2_WRITE_TOKEN':'w'*40,
          'COURSE_TRANSCRIPT_V2_OWNER_ACTOR':'trusted-operator',
          'COURSE_TRANSCRIPT_V2_LEDGER_PATH':str(self.root/'ledger.sqlite'),
          'COURSE_TRANSCRIPT_V2_GRANT_SECRET':'h'*40,
          'COURSE_TRANSCRIPT_V2_MAX_APPROVAL_CENTS':'1000',
        })
        self.patch.start();self.addCleanup(self.patch.stop)
        self.client=TestClient(app)
        self.write={'Authorization':'Bearer '+'w'*40}
        self.read={'Authorization':'Bearer '+'s'*40}

    def test_policy_requires_authenticated_owner(self):
        payload={'cap_per_folder_cents':600,'valid_through':'2026-12-31',
                 'enabled':True,'confirm':True}
        self.assertEqual(self.client.post('/api/v1/golden-v2/auto-budget',json=payload).status_code,401)
        r=self.client.post('/api/v1/golden-v2/auto-budget',json=payload,headers=self.write)
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['enabled'])
        status=self.client.get('/api/v1/golden-v2/auto-budget',headers=self.read)
        self.assertEqual(status.status_code,200)
        self.assertTrue(status.json()['configured'])
        self.assertEqual(status.json()['cap_per_submitted_folder_cents'],600)

    def test_over_600_policy_configuration_refused(self):
        r=self.client.post('/api/v1/golden-v2/auto-budget',headers=self.write,json={
          'cap_per_folder_cents':601,'valid_through':'2026-12-31',
          'enabled':True,'confirm':True})
        self.assertEqual(r.status_code,422)
        self.assertEqual(self.client.get('/api/v1/golden-v2/auto-budget',headers=self.read).json()['configured'],False)

    def test_auto_job_endpoint_fail_closed_while_paid_api_disabled(self):
        with patch.dict('os.environ',{'COURSE_TRANSCRIPT_V2_PAID_API_ENABLED':'false'}):
            response=self.client.post('/api/v1/golden-v2/jobs/v2p_'+'a'*32+'/auto-budget/authorize',
                    headers=self.write,json={'submitted_folder_path':'gdrive:lectures/new','confirm':True})
        self.assertEqual(response.status_code,409)


if __name__=='__main__':unittest.main()
