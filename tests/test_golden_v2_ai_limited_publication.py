"""No-network publication/rollback/owner-signature tests."""
import hashlib
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_ai_limited_closeout import (
    TIER,register_proposal,
)
from app.pipeline.golden_v2_ai_limited_publication import publish_signed_ai_limited
from tests.test_golden_v2_ai_limited_closeout import AILimitedCloseoutTests


def sign(payload):
    return {'grant':payload,'signature':hashlib.sha256(
        json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}


class FakeDrive:
    def __init__(self):
        self.files = {'f123':{
            'id':'f123','name':'source.wav','version':'7',
            'size':str(len(b"normalized synthetic test audio, not real speech")),
            'md5Checksum':'unit-md5','parents':['lecture-parent'],
            'trashed':False,'appProperties':{}
        }}
        self.content = {}
        self.count=0
        self.fail_second=False

    def named(self,parent,name):
        return [{'id':v['id'],'name':v['name']} for v in self.files.values()
                if v['name']==name and parent in v.get('parents',[])
                and not v.get('trashed')]

    def get(self,file_id):
        return self.files.get(file_id)

    def generate_id(self):
        self.count+=1
        return 'new-'+str(self.count)

    def create_pending(self,file_id,parent,name,content,job_id):
        if self.fail_second and file_id=="new-2":
            raise V2Conflict('simulated second-file temporary outage')
        self.files[file_id]={
            'id':file_id,'name':name,'parents':[parent],'trashed':False,
            'appProperties':{'golden_v2_job':job_id}
        }
        self.content[file_id]=content

    def readback(self,file_id):
        return self.content.get(file_id,b'')

    def write_pending_content(self,file_id,content):
        self.content[file_id]=content

    def promote(self,file_id,name):
        self.files[file_id]['name']=name


class LimitedPublisherTests(AILimitedCloseoutTests):
    def setUp(self):
        super().setUp()
        self.manifest=self.proposal()
        register_proposal(self.root,self.manifest)
        self.grant={
            'job_id':self.job_id,'manifest_sha256':self.manifest['manifest_sha256'],
            'quality_tier':TIER,'source_sha256':self.sha,
            'output_names':list(self.names),'approve_delivery':True,
            'actor':'authenticated-owner','reference':'explicit-owner-limited-quality-review',
            'source_version_at_approval':'7',
            'confirm_listened':None,
        }
        self.grant_path=(self.folder/'owner-ai-limited-grant-'/
                         (self.manifest['manifest_sha256']+'.json'))
        self.grant_path.parent.mkdir()
        self.grant_path.write_text(json.dumps(sign(self.grant)))
        self.gateway=FakeDrive()

    def publish(self):
        with patch('app.pipeline.golden_v2_ai_limited_publication.signed_grant',sign):
            return publish_signed_ai_limited(
                root=self.root,job_id=self.job_id,
                manifest_sha=self.manifest['manifest_sha256'],
                lock_root=self.root,gateway=self.gateway,
            )

    def test_two_files_posted_readback_and_idempotent(self):
        out=self.publish()
        self.assertEqual(out['quality_tier'],TIER)
        self.assertTrue(out['readback_verified'])
        self.assertFalse(out['public_links_created'])
        self.assertEqual([f['name'] for f in out['files']],list(self.names))
        self.assertEqual(self.gateway.count,2)
        self.assertEqual(self.publish(),out)
        self.assertEqual(self.gateway.count,2)
        self.assertEqual(self.ledger.status(self.job_id)['status'],'NEEDS_REVIEW')

    def test_interrupted_second_upload_reuses_ids(self):
        self.gateway.fail_second=True
        with self.assertRaisesRegex(V2Conflict,'temporary outage'):
            self.publish()
        self.assertEqual(self.gateway.count,2)
        self.gateway.fail_second=False
        result=self.publish()
        self.assertEqual(self.gateway.count,2)
        self.assertEqual(len(result['files']),2)

    def test_unrelated_existing_filename_blocks_all_mutations(self):
        self.gateway.files['someone-else']={
            'id':'someone-else','name':self.names[1],
            'parents':['lecture-parent'],'trashed':False}
        with self.assertRaisesRegex(V2Conflict,'destination name already exists'):
            self.publish()
        # First staging ID may be preallocated, but no remote content may POST.
        self.assertEqual(len(self.gateway.content),0)

    def test_wrong_owner_signature_rejected(self):
        envelope=json.loads(self.grant_path.read_text())
        envelope['signature']='f'*64
        self.grant_path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(V2Conflict,'signature'):
            self.publish()
        self.assertEqual(self.gateway.count,0)

    def test_changed_source_version_stops_before_post(self):
        self.gateway.files['f123']['version']='8'
        with self.assertRaisesRegex(V2Conflict,'source'):
            self.publish()
        self.assertEqual(self.gateway.count,0)

    def test_metadata_revision_changes_only_when_owner_signed_new_revision(self):
        self.gateway.files['f123']['version']='8'
        payload=dict(self.grant,source_version_at_approval='8')
        self.grant_path.write_text(json.dumps(sign(payload)))
        result=self.publish()
        self.assertTrue(result['readback_verified'])
        self.assertEqual(len(result['files']),2)

    def test_md5_changed_even_with_owner_new_revision_stops(self):
        self.gateway.files['f123']['version']='8'
        self.gateway.files['f123']['md5Checksum']='different-content'
        payload=dict(self.grant,source_version_at_approval='8')
        self.grant_path.write_text(json.dumps(sign(payload)))
        with self.assertRaisesRegex(V2Conflict,'source'):
            self.publish()
        self.assertEqual(self.gateway.count,0)

    def test_changed_original_srt_stops_before_post(self):
        self.srt.write_text(self.srt.read_text()+'\n')
        with self.assertRaisesRegex(V2Conflict,'candidate has changed'):
            self.publish()
        self.assertEqual(self.gateway.count,0)


if __name__=='__main__':
    unittest.main()
