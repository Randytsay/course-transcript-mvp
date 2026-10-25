"""The standing-policy CLI must use the same private owner token path."""
import io
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from scripts.golden_v2_owner_client import main


class Response:
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def read(self,*args):return b'{"configured":true,"enabled":true}'


class Opener:
    def __init__(self):self.request=None
    def open(self,request,timeout=None):
        self.request=request
        return Response()


class AutoSixOwnerCLITests(unittest.TestCase):
    def invoke(self,action,payload=None):
        opener=Opener()
        argv=['owner',action]
        if action=='auto-budget-authorize':argv.append('v2p_'+'a'*32)
        if payload is not None:argv.append('--request-stdin')
        config={'COURSE_TRANSCRIPT_V2_OWNER_API_URL':'http://127.0.0.1:8816',
                'COURSE_TRANSCRIPT_V2_STATUS_TOKEN':'s'*40,
                'COURSE_TRANSCRIPT_V2_WRITE_TOKEN':'w'*40}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,config))
            stack.enter_context(patch('sys.argv',argv))
            stack.enter_context(patch('scripts.golden_v2_owner_client.build_opener',return_value=opener))
            if payload is not None:
                stack.enter_context(patch('sys.stdin',io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode()))))
            stack.enter_context(patch('sys.stdout',io.StringIO()))
            self.assertEqual(main(),0)
        return opener.request

    def test_authenticated_status(self):
        r=self.invoke('auto-budget-status')
        self.assertTrue(r.full_url.endswith('/api/v1/golden-v2/auto-budget'))
        self.assertEqual(r.get_method(),'GET')
        self.assertEqual(r.get_header('Authorization'),'Bearer '+'s'*40)

    def test_configure_policy_scoped_owner_only(self):
        data={'cap_per_folder_cents':600,'valid_through':'2026-12-31',
              'enabled':True,'confirm':True}
        r=self.invoke('auto-budget-configure',data)
        self.assertTrue(r.full_url.endswith('/api/v1/golden-v2/auto-budget'))
        self.assertEqual(r.get_header('Authorization'),'Bearer '+'w'*40)
        self.assertEqual(json.loads(r.data)['cap_per_folder_cents'],600)

    def test_new_job_auto_authorize_binds_folder(self):
        r=self.invoke('auto-budget-authorize',{'submitted_folder_path':'gdrive:lectures/new','confirm':True})
        self.assertTrue(r.full_url.endswith('/api/v1/golden-v2/jobs/v2p_'+'a'*32+'/auto-budget/authorize'))
        self.assertEqual(r.get_method(),'POST')
        self.assertEqual(json.loads(r.data)['submitted_folder_path'],'gdrive:lectures/new')


if __name__=='__main__':unittest.main()
