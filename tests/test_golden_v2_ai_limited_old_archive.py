"""No-network, no-listening exact-revision archive contract."""
import hashlib
import json
import unittest
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from tests.test_golden_v2_ai_limited_publication import LimitedPublisherTests, sign


class OldArchiveTests(unittest.TestCase):
    def setUp(self):
        self.fixture = LimitedPublisherTests('test_two_files_posted_readback_and_idempotent')
        self.fixture.setUp()
        self.__dict__.update(self.fixture.__dict__)
        self.fixture.publish()
        self.old = {}
        for ext in ('srt', 'txt'):
            name = 'human-original.' + ext
            file_id = 'old-' + ext
            data = ('historical ' + ext).encode()
            self.gateway.files[file_id] = {'id': file_id, 'name': name,
                'parents': ['lecture-parent'], 'version': '3', 'trashed': False}
            self.gateway.content[file_id] = data
            self.old[ext] = {'file_id': file_id, 'name': name,
                'version': '3', 'sha256': hashlib.sha256(data).hexdigest(),
                'backup_name': 'human-original(舊).' + ext}
        self.grant = {'purpose': 'ARCHIVE_DELIVERED_AI_LIMITED_OLD_REVISIONS',
            'job_id': self.job_id, 'manifest_sha256': self.manifest['manifest_sha256'],
            'source_sha256': self.sha, 'source_version_at_approval': '7',
            'actor': 'owner', 'reference': 'explicit-archive', 'old_files': list(self.old.values())}
        self.path = self.folder / 'owner-ai-limited-old-archive-grant' / (self.manifest['manifest_sha256'] + '.json')
        self.path.parent.mkdir()
        self.path.write_text(json.dumps(sign(self.grant)))

    def tearDown(self):
        self.fixture.tearDown()

    def archive(self):
        from app.pipeline.golden_v2_ai_limited_old_archive import archive_signed_old_revisions
        with patch('app.pipeline.golden_v2_ai_limited_old_archive.signed_grant', sign):
            return archive_signed_old_revisions(root=self.root, job_id=self.job_id,
                manifest_sha=self.manifest['manifest_sha256'], lock_root=self.root,
                gateway=self.gateway)

    def test_exact_old_ids_archived_and_idempotent_receipt(self):
        receipt = self.archive()
        self.assertEqual([self.gateway.files[x['file_id']]['name'] for x in self.old.values()],
                         [x['backup_name'] for x in self.old.values()])
        self.assertEqual(self.archive(), receipt)
        self.assertEqual(receipt['state'], 'ARCHIVED')
        receipt_path = self.folder/'golden-v2'/'ai-limited-old-archive-receipts'/(self.manifest['manifest_sha256']+'.json')
        self.assertEqual(json.loads(receipt_path.read_text()), receipt)

    def test_collision_requires_signed_timestamped_name(self):
        self.gateway.files['third'] = {'id': 'third', 'name': self.old['srt']['backup_name'],
            'parents': ['lecture-parent'], 'trashed': False}
        with self.assertRaises(V2Conflict): self.archive()
        self.assertEqual(self.gateway.files['old-txt']['name'], self.old['txt']['name'])
        self.old['srt']['backup_name'] = 'human-original(舊)-20261116T120000Z.srt'
        self.path.write_text(json.dumps(sign(self.grant)))
        self.archive()
        self.assertEqual(self.gateway.files['third']['name'], 'human-original(舊).srt')

    def test_editor_change_blocks_all_renames(self):
        self.gateway.files['old-txt']['version'] = '4'
        with self.assertRaises(V2Conflict): self.archive()
        self.assertEqual(self.gateway.files['old-srt']['name'], self.old['srt']['name'])

    def test_source_drift_blocks_all_renames(self):
        self.gateway.files['f123']['version'] = '8'
        with self.assertRaises(V2Conflict): self.archive()
        self.assertEqual(self.gateway.files['old-srt']['name'], self.old['srt']['name'])

    def test_delivered_file_drift_blocks_all_renames(self):
        self.gateway.content['new-2'] = b'edited delivery'
        with self.assertRaises(V2Conflict): self.archive()
        self.assertEqual(self.gateway.files['old-srt']['name'], self.old['srt']['name'])

    def test_partial_rename_resumes_after_verified_readback(self):
        promote = self.gateway.promote
        count = [0]
        def interrupted(file_id, name):
            count[0] += 1
            if count[0] == 2: raise V2Conflict('temporary outage')
            return promote(file_id, name)
        with patch.object(self.gateway, 'promote', interrupted):
            with self.assertRaises(V2Conflict): self.archive()
        self.assertEqual(self.gateway.files['old-srt']['name'], self.old['srt']['backup_name'])
        self.assertEqual(self.gateway.files['old-txt']['name'], self.old['txt']['name'])
        self.archive()
        self.assertEqual(self.gateway.files['old-txt']['name'], self.old['txt']['backup_name'])

    def test_owner_route_issues_separate_exact_revision_grant(self):
        from app.golden_v2_review_routes import (
            AILimitedOldArchiveApproval, authorize_ai_limited_old_archive,
        )
        body = AILimitedOldArchiveApproval(manifest_sha256=self.manifest['manifest_sha256'],
            old_files=list(self.old.values()), review_reference='explicit-archive', confirm=True)
        self.path.unlink()
        with (patch('app.golden_v2_review_routes._authorized_write_database',
                    return_value=(self.root/'ledger.sqlite','owner',None)),
              patch('app.golden_v2_review_routes._limited_enabled'),
              patch('app.pipeline.golden_v2_delivery.DriveDeliveryGateway',
                    return_value=self.gateway),
              patch('app.golden_v2_review_routes.signed_grant',sign)):
            authorize_ai_limited_old_archive(self.job_id,body,authorization='test')
        self.assertEqual(json.loads(self.path.read_text())['grant']['old_files'],
                         list(self.old.values()))

    def test_bad_signature_cannot_archive(self):
        self.path.write_text(json.dumps({'grant': self.grant, 'signature': '0'*64}))
        with self.assertRaises(V2Conflict): self.archive()
        self.assertEqual(self.gateway.files['old-srt']['name'], self.old['srt']['name'])
