"""Provider-free Drive option-3 protocol tests; no network or deployment."""
import hashlib
import unittest
from app.pipeline.golden_v2_multipart_pilot import (
    build_exact_id_multipart, verify_remote_exact, classify_http_outcome,
    MultipartSafetyHold)

class MultipartPilotTests(unittest.TestCase):
    def setUp(self):
        self.data='清淨心\n'.encode()
        self.args=dict(file_id='file1234',parent_id='parent1234',
            name='.pending_1',pending=True,media=self.data,
            job_id='v2p_'+'a'*32,manifest_sha256='b'*64,
            artifact_extension='.txt',
            expected_media_sha256=hashlib.sha256(self.data).hexdigest())
    def req(self, **kw):
        return build_exact_id_multipart(**(self.args|kw))
    def remote(self, req, **kw):
        return {**req.metadata,'version':'7','trashed':False,
                'size':str(req.expected_size),'md5Checksum':req.expected_md5,**kw}

    def test_single_request_has_expected_content(self):
        req=self.req()
        self.assertEqual(req.method,'POST')
        self.assertEqual(req.params['uploadType'],'multipart')
        self.assertEqual(req.body.count(self.data),1)
        self.assertIn(b'multipart',req.content_type.encode())
        self.assertIn(b'text/plain',req.body)
        self.assertIn(b'"golden_v2_auto_manifest"',req.body)

    def test_409_requires_exact_remote_validation(self):
        req=self.req()
        outcome=classify_http_outcome(status_code=409,request=req,
                    remote=self.remote(req),readback=self.data)
        self.assertTrue(outcome.startswith('VERIFIED'))
        for blob in (None,b'wrong'):
            self.assertTrue(classify_http_outcome(status_code=409,request=req,
                 remote=self.remote(req),readback=blob).startswith('HOLD'))

    def test_404_always_hold(self):
        req=self.req()
        self.assertTrue(classify_http_outcome(status_code=404,request=req,
             remote=None,readback=None).startswith('HOLD'))
        self.assertTrue(classify_http_outcome(status_code=404,request=req,
             remote=self.remote(req),readback=self.data).startswith('HOLD'))

    def test_error_response_reconciles_only_with_exact_remote(self):
        req=self.req()
        for status in (None,500,502,503,504):
            self.assertTrue(classify_http_outcome(status_code=status,request=req,
                remote=None,readback=None).startswith('HOLD'))
            self.assertTrue(classify_http_outcome(status_code=status,request=req,
                remote=self.remote(req),readback=self.data).startswith('VERIFIED'))
    def test_tamper_parent_owner_size_md5_name_is_rejected(self):
        req=self.req()
        changes=[{'id':'wrong-file'},{'name':'other.srt'},
                 {'parents':['another-parent']},{'size':'200'},
                 {'md5Checksum':'0'*32},{'version':'unknown'},
                 {'trashed':True},{'appProperties':{'golden_v2_job':'wrong'}},
                 {'mimeType':'application/octet-stream'}]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(verify_remote_exact(req,
                    remote=self.remote(req,**change),readback=self.data))

    def test_invalid_source_content_and_name_blocks_before_io(self):
        cases=[{'file_id':'bad/id'},{'parent_id':'x'},
               {'manifest_sha256':'bad'},{'job_id':'not-a-job'},
               {'expected_media_sha256':'f'*64},{'artifact_extension':'.exe'},
               {'name':'evil\nname.txt'},{'name':'../x.txt'}]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(MultipartSafetyHold): self.req(**changes)

    def test_supported_media_types_and_actual_final_name(self):
        types={'.srt':'application/x-subrip','.txt':'text/plain',
               '.json':'application/json','.md':'text/markdown'}
        for ext,mime in types.items():
            with self.subTest(ext=ext):
                req=self.req(name='lesson'+ext,pending=False,
                             artifact_extension=ext)
                self.assertEqual(req.metadata['mimeType'],mime)
        with self.assertRaises(MultipartSafetyHold):
            self.req(name='lesson.txt',pending=False,artifact_extension='.srt')

    def test_id_reuse_does_not_count_as_proof_of_validity(self):
        req=self.req()
        self.assertTrue(classify_http_outcome(status_code=409,
            request=req,remote=self.remote(req,appProperties={'wrong':'owner'}),
            readback=self.data).startswith('HOLD'))
