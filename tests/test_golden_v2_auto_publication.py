"""Four-asset fail-closed publication and receipt tests (no Drive network)."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_auto_closeout import _wire, prepare_auto_acceptance
from app.pipeline.golden_v2_auto_publication import publish_signed_auto_acceptance
from app.providers.golden_v2_auto_acceptance import render_human_review_advisory
from tests.test_golden_v2_auto_closeout import CloseoutTests


def sign(payload):
    return {'grant':payload,'signature':hashlib.sha256(_wire(payload)).hexdigest()}


class FakeDrive:
    def __init__(self,source_bytes):
        self.source_bytes=source_bytes
        self.files={'source-id':{'id':'source-id','version':'7','size':str(len(source_bytes)),
             'md5Checksum':'original-md5','name':'lesson.mp3','parents':['original-folder'],
             'trashed':False}}
        self.data={};self.generated=0;self.block_fourth=False;self.promotions=[]

    def get(self,file_id): return self.files.get(file_id)
    def named(self,parent,name):
        return [{'id':x['id'],'name':x['name']} for x in self.files.values()
                if x.get('name')==name and x.get('parents')==[parent] and not x.get('trashed')]
    def generate_id(self):
        self.generated+=1
        return 'allocated-'+str(self.generated)
    def create_pending(self,file_id,parent,name,content,job_id):
        if self.block_fourth and file_id=='allocated-4':
            raise V2Conflict('simulated fourth-file outage')
        self.files[file_id]={'id':file_id,'name':name,'parents':[parent],
                        'appProperties':{'golden_v2_job':job_id},'trashed':False}
        self.data[file_id]=content
    def readback(self,file_id):return self.data.get(file_id,b'')
    def write_pending_content(self,file_id,raw):self.data[file_id]=raw
    def promote(self,file_id,name):
        self.files[file_id]['name']=name
        self.promotions.append(file_id)


class AutoPublisherTests(unittest.TestCase):
    def setUp(self):
        CloseoutTests.setUp(self)
        original=json.loads((self.jobdir/'source-preflight.json').read_text())
        original['drive_observation']['size_bytes']=len(self.flac)
        original['drive_observation']['downloaded_md5']='original-md5'
        original['original_md5_checksum']='original-md5'
        original['drive_source_path']='gdrive:original-folder/lesson.mp3'
        (self.jobdir/'source-preflight.json').write_text(json.dumps(original))
        folder=self.jobdir/'golden-v2';folder.mkdir(exist_ok=True)
        self.srt0=folder/self.srt.name;self.srt0.write_bytes(self.srt.read_bytes())
        self.txt0=folder/self.txt.name;self.txt0.write_bytes(self.txt.read_bytes())
        self.result['human_review_advisory']['recommended']=True
        self.report_json=folder/'acceptance.json'
        self.report_json.write_bytes(_wire(self.result))
        self.report_md=folder/'human-review.md'
        self.report_md.write_bytes(render_human_review_advisory(self.result,lesson_title='測試課程').encode())
        self.extra_names=('output_自動驗收_驗收.json','output_自動驗收_人工建議複核.md')
        self.prepared=prepare_auto_acceptance(root=self.root,job_id=self.job,
            result=self.result,srt_path=self.srt0,txt_path=self.txt0,
            target_names=self.names,report_json_path=self.report_json,
            report_md_path=self.report_md,report_names=self.extra_names,lesson_title='測試課程')
        self.manifest=self.prepared['manifest']
        self.grant={**CloseoutTests.grant(self,self.manifest),'source_version_at_approval':'7',
                    'human_listening_attested':False}
        grant_path=self.jobdir/'owner-auto-delivery-grants'/(
            self.manifest['manifest_sha256']+'.json')
        grant_path.parent.mkdir(exist_ok=True)
        grant_path.write_bytes(_wire(sign(self.grant)))
        self.drive=FakeDrive(self.flac)

    def publish(self):
        with patch('app.pipeline.golden_v2_auto_publication.signed_grant',sign):
            return publish_signed_auto_acceptance(root=self.root,job_id=self.job,
                 manifest_sha=self.manifest['manifest_sha256'],lock_root=self.root,
                 gateway=self.drive)

    def test_four_files_and_idempotent_receipt(self):
        result=self.publish()
        self.assertEqual(result['state'],'AUTO_DELIVERED')
        self.assertEqual(len(result['remote_files']),4)
        self.assertFalse(result['golden_qa_pass'])
        self.assertFalse(result['human_listening_attested'])
        self.assertEqual(self.drive.generated,4)
        promotions=list(self.drive.promotions)
        self.assertEqual(self.publish()['manifest_sha256'],result['manifest_sha256'])
        self.assertEqual(self.drive.promotions,promotions)
        self.assertEqual(self.ledger.status(self.job)['status'],'PROCESSING')

    def test_fourth_file_failure_is_not_delivered_and_retry_reuses_ids(self):
        self.drive.block_fourth=True
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,4)
        self.assertFalse(self.drive.promotions)
        self.assertEqual(self.prepared['state'],'PREPARED')
        self.drive.block_fourth=False
        result=self.publish()
        self.assertEqual(result['state'],'AUTO_DELIVERED')
        self.assertEqual(self.drive.generated,4)

    def test_existing_editor_file_is_never_overwritten(self):
        self.drive.files['original-srt']={'id':'original-srt',
            'name':self.names[0],'parents':['original-folder'],'trashed':False}
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)

    def test_changed_audio_version_blocks_before_drive_write(self):
        self.drive.files['source-id']['version']='8'
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)

    def test_forged_grant_blocks(self):
        grant_path=self.jobdir/'owner-auto-delivery-grants'/(
            self.manifest['manifest_sha256']+'.json')
        envelope=json.loads(grant_path.read_text())
        envelope['signature']='0'*64
        grant_path.write_text(json.dumps(envelope))
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)

    def test_modified_companion_blocks_before_publication(self):
        self.report_md.write_text('forged')
        with self.assertRaises(V2Conflict):self.publish()
        self.assertEqual(self.drive.generated,0)
