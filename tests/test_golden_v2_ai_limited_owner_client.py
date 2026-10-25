"""Token-safe CLI route tests, using only dummy tokens; no network access."""
from __future__ import annotations

from contextlib import ExitStack
import io
import json
import os
from unittest import TestCase
from unittest.mock import patch

from scripts.golden_v2_owner_client import main


JOB = 'v2p_' + 'a'*32


class Response:
    def __enter__(self): return self
    def __exit__(self,*args): return None
    def read(self,*args): return b'{"state":"PREPARED"}'


class Opener:
    def __init__(self): self.request = None
    def open(self,request,timeout=None):
        self.request=request
        return Response()


class AILimitedOwnerClientTests(TestCase):
    def invoke(self,action,payload=None):
        opener=Opener()
        argv=['owner','%s'%action,JOB]
        if payload is not None: argv.append('--request-stdin')
        context={
            'COURSE_TRANSCRIPT_V2_OWNER_API_URL':'http://127.0.0.1:8816',
            'COURSE_TRANSCRIPT_V2_STATUS_TOKEN':'status-dummy-'+'s'*40,
            'COURSE_TRANSCRIPT_V2_WRITE_TOKEN':'write-dummy-'+'w'*40,
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,context))
            stack.enter_context(patch('sys.argv',argv))
            stack.enter_context(patch('scripts.golden_v2_owner_client.build_opener',return_value=opener))
            stdin=io.TextIOWrapper(io.BytesIO(json.dumps(payload or {}).encode()))
            stack.enter_context(patch('sys.stdin',stdin))
            stdout=io.StringIO()
            stack.enter_context(patch('sys.stdout',stdout))
            self.assertEqual(main(),0)
            self.assertNotIn('dummy-',stdout.getvalue())
        return opener.request

    def test_status_route_get(self):
        request=self.invoke('ai-limited-status')
        self.assertEqual(request.get_method(),'GET')
        self.assertTrue(request.full_url.endswith('/jobs/'+JOB+'/ai-limited/status'))

    def test_owner_writes_use_exact_endpoints_and_bounded_confirm(self):
        for action,path in (
            ('ai-limited-prepare','prepare'),
            ('ai-limited-authorize-publication','authorize-publication'),
            ('ai-limited-publish','publish'),
        ):
            with self.subTest(action=action):
                request=self.invoke(action,{'confirm':True,'revision':7})
                self.assertEqual(request.get_method(),'POST')
                self.assertTrue(request.full_url.endswith('/jobs/'+JOB+'/ai-limited/'+path))
                self.assertTrue(json.loads(request.data)['confirm'])


if __name__ == '__main__':
    import unittest
    unittest.main()
