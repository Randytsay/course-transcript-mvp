"""Owner HTTP integration: default-off, no paid services, four-file receipt."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.golden_v2_chat_api import app
from app.pipeline.golden_v2_auto_closeout import _schema
from tests.test_golden_v2_auto_closeout import CloseoutTests
from tests.test_golden_v2_auto_publication import FakeDrive


class AutoRouteTests(unittest.TestCase):
    def setUp(self):
        CloseoutTests.setUp(self)
        obs=json.loads((self.jobdir/'source-preflight.json').read_text())
        obs['drive_observation'].update(size_bytes=len(self.flac),downloaded_md5='original-md5')
        obs['original_md5_checksum']='original-md5'
        obs['drive_source_path']='gdrive:original-folder/lesson.mp3'
        (self.jobdir/'source-preflight.json').write_text(json.dumps(obs))
        folder=self.jobdir/'golden-v2';folder.mkdir(exist_ok=True)
        (folder/'candidate.srt').write_bytes(self.srt.read_bytes())
        (folder/'candidate.txt').write_bytes(self.txt.read_bytes())
        self.drive=FakeDrive(self.flac)
        self.client=TestClient(app)
        self.read='read-test-token-'+'r'*45;self.write='write-test-token-'+'w'*45
        self.headers={'Authorization':'Bearer '+self.write}
        self.url=f'/api/v1/golden-v2/jobs/{self.job}/auto-delivery'
        self.env=patch.dict(os.environ,{
            'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED':'true',
            'COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED':'true',
            'COURSE_TRANSCRIPT_V2_AUTO_DELIVERY_API_ENABLED':'false',
            'COURSE_TRANSCRIPT_V2_STATUS_TOKEN':self.read,
            'COURSE_TRANSCRIPT_V2_WRITE_TOKEN':self.write,
            'COURSE_TRANSCRIPT_V2_OWNER_ACTOR':'owner-test',
            'COURSE_TRANSCRIPT_V2_GRANT_SECRET':'x'*48,
            'COURSE_TRANSCRIPT_V2_LEDGER_PATH':str(self.root/'ledger.sqlite'),
            'DRIVE_GLOBAL_MIN_INTERVAL_SECONDS':'0',
        });self.env.start();self.addCleanup(self.env.stop)

    def body(self):
        return {'revision':self.ledger.status(self.job)['revision'],
            'source_sha256':self.source,'srt_filename':'candidate.srt',
            'txt_filename':'candidate.txt','lesson_title':'清淨心','confirm':True}

    def test_api_disabled_without_auth_and_does_not_prepare(self):
        self.assertEqual(self.client.post(self.url+'/prepare',json=self.body()).status_code,401)
        self.assertEqual(self.client.post(self.url+'/prepare',headers=self.headers,json=self.body()).status_code,404)
        status=self.client.get(self.url+'/status',headers={'Authorization':'Bearer '+self.read})
        self.assertEqual(status.status_code,200)
        self.assertEqual(status.json()['deliveries'],[])

    def test_prepare_authorize_publish_and_readback_without_listening(self):
        with patch.dict(os.environ,{'COURSE_TRANSCRIPT_V2_AUTO_DELIVERY_API_ENABLED':'true'}):
            with patch('app.golden_v2_auto_routes.subprocess.run',return_value=SimpleNamespace(stdout='3.000')):
                start=self.client.post(self.url+'/prepare',json=self.body(),headers=self.headers)
            self.assertEqual(start.status_code,200,start.text)
            self.assertFalse(start.json()['human_listening_required'])
            self.assertEqual(len(start.json()['output_names']),4)
            manifest_sha=start.json()['manifest_sha256']
            with patch('app.golden_v2_auto_routes.DriveDeliveryGateway',return_value=self.drive):
                approved=self.client.post(self.url+'/authorize-publication',headers=self.headers,
                    json={'manifest_sha256':manifest_sha,'revision':self.ledger.status(self.job)['revision'],
                          'review_reference':'approved source-bound auto delivery','confirm':True})
            self.assertEqual(approved.status_code,200,approved.text)
            with patch('app.pipeline.golden_v2_auto_publication.DriveDeliveryGateway',return_value=self.drive):
                published=self.client.post(self.url+'/publish',headers=self.headers,
                    json={'manifest_sha256':manifest_sha,'confirm':True})
            self.assertEqual(published.status_code,200,published.text)
            self.assertEqual(published.json()['state'],'AUTO_DELIVERED')
            self.assertFalse(published.json()['golden_qa_pass'])
            self.assertEqual(self.drive.generated,4)
            self.assertEqual(self.ledger.status(self.job)['status'],'PROCESSING')
            result=self.client.get(self.url+'/status',headers={'Authorization':'Bearer '+self.read})
            self.assertEqual(result.json()['deliveries'][0]['state'],'AUTO_DELIVERED')

    def test_bad_srt_blocks_without_creating_delivery_receipt(self):
        (self.jobdir/'golden-v2'/'candidate.txt').write_text('different')
        with patch.dict(os.environ,{'COURSE_TRANSCRIPT_V2_AUTO_DELIVERY_API_ENABLED':'true'}):
            with patch('app.golden_v2_auto_routes.subprocess.run',return_value=SimpleNamespace(stdout='3.000')):
                res=self.client.post(self.url+'/prepare',headers=self.headers,json=self.body())
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['status'],'AUTO_BLOCKED_TECHNICAL')
        status=self.client.get(self.url+'/status',headers={'Authorization':'Bearer '+self.read})
        self.assertEqual(status.json()['deliveries'],[])


if __name__=='__main__': unittest.main()
