from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.golden_v2_chat_api import app
from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.operations.runtime_heartbeat import write_service_heartbeat
from scripts.golden_v2_chat_client import load_private_config
from scripts import golden_v2_owner_client as client


class ChatEntryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.data = self.root / 'data'; self.v2 = self.data / 'golden-v2'
        self.db = self.v2 / 'ledger.sqlite'; GoldenV2Ledger(self.db)
        self.read = 'private-read-token-' + 'r' * 40
        self.write = 'private-write-token-' + 'w' * 40
        self.env = patch.dict(os.environ, {
            'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED': 'true',
            'COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED': 'true',
            'COURSE_TRANSCRIPT_V2_STATUS_TOKEN': self.read,
            'COURSE_TRANSCRIPT_V2_WRITE_TOKEN': self.write,
            'COURSE_TRANSCRIPT_V2_OWNER_ACTOR': 'chat-owner',
            'COURSE_TRANSCRIPT_V2_LEDGER_PATH': str(self.db),
            'COURSE_TRANSCRIPT_V2_RELEASE_SHA': 'a' * 40,
        }); self.env.start(); self.addCleanup(self.env.stop)
        self.client = TestClient(app); self.headers = {'Authorization': 'Bearer ' + self.read}

    def test_service_status_is_authenticated_and_no_store_without_credential_leak(self):
        self.assertEqual(self.client.get('/api/v1/golden-v2/service').status_code, 401)
        response = self.client.get('/api/v1/golden-v2/service', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        result = response.json()
        self.assertEqual(result['release_sha'], 'a' * 40)
        self.assertFalse(result['capabilities']['paid_execution'])
        self.assertFalse(result['capabilities']['drive_publication'])
        self.assertNotIn(self.read, response.text); self.assertNotIn(self.write, response.text)
        self.assertNotIn(str(self.db), response.text)
        with patch.dict(os.environ, {'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED': 'false'}):
            self.assertEqual(self.client.get('/api/v1/golden-v2/service', headers=self.headers).status_code, 404)

    def test_worker_heartbeat_is_observed_not_assumed(self):
        self.assertEqual(self.client.get('/api/v1/golden-v2/service', headers=self.headers).json()['intake_worker']['state'], 'UNVERIFIED')
        write_service_heartbeat(self.data, 'golden-v2-intake')
        self.assertEqual(self.client.get('/api/v1/golden-v2/service', headers=self.headers).json()['intake_worker']['state'], 'running')
        path = self.data / 'runtime/golden-v2-intake.heartbeat.json'
        payload = json.loads(path.read_text()); payload['updated_at'] = '2000-01-01T00:00:00+00:00'; path.write_text(json.dumps(payload))
        self.assertEqual(self.client.get('/api/v1/golden-v2/service', headers=self.headers).json()['intake_worker']['state'], 'STALE_OR_STOPPED')

    def test_nonpaid_entry_does_not_expose_approval_or_attested_job_creation(self):
        headers = {'Authorization': 'Bearer ' + self.write}
        for path in ['/jobs', '/source-preflight', '/jobs/v2_' + 'a'*32 + '/approval', '/jobs/v2_' + 'a'*32 + '/repair-approval']:
            self.assertIn(self.client.post('/api/v1/golden-v2' + path, json={}, headers=headers).status_code, {404, 405})
        self.assertEqual(self.client.get('/api/v1/golden-v2/jobs', headers=self.headers).json(), {'jobs': []})
        self.assertEqual(self.client.get('/docs').status_code, 404)

    def test_drive_health_is_read_only_authenticated_and_redacted(self):
        with patch('app.golden_v2_chat_api.drive_health', return_value={'status':'ok','paid_operation_started':False}) as health:
            self.assertEqual(self.client.get('/api/v1/golden-v2/drive-health').status_code, 401)
            health.assert_not_called()
            response = self.client.get('/api/v1/golden-v2/drive-health', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        health.assert_called_once()
        self.assertFalse(response.json()['paid_operation_started'])

    def test_preview_is_the_existing_authenticated_drive_path(self):
        with patch('app.golden_v2_routes.preview_drive_link', return_value={'files': [], 'provider_calls_started': False}) as preview:
            result = self.client.post('/api/v1/golden-v2/link-preview', json={'link':'https://drive.google.com/drive/folders/folder0123456789','recursive':False}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        preview.assert_called_once()

    def test_quote_uses_write_token_fresh_preview_and_no_caller_prices(self):
        source = dict(file_id='drive-file-12345',version='7',source_path='gdrive:folder/a.MP3',
                      name='a.MP3',size_bytes=3,md5_checksum='abc')
        body = dict(link='https://drive.google.com/drive/folders/folder0123456789',
                    file_id=source['file_id'],preview_sha256='b'*64,confirm=True)
        with patch('app.golden_v2_chat_api.preview_drive_link',return_value={'files':[source],'preview_sha256':'b'*64}):
            read_attempt=self.client.post('/api/v1/golden-v2/quotes',json=body,headers=self.headers)
            self.assertEqual(read_attempt.status_code,401)
            headers={'Authorization':'Bearer '+self.write}
            result=self.client.post('/api/v1/golden-v2/quotes',json=body,headers=headers)
            self.assertEqual(result.status_code,202)
            quote_id=result.json()['quote_id']
            self.assertEqual(self.client.get('/api/v1/golden-v2/quotes/'+quote_id,headers=self.headers).json()['state'],'PENDING')
            self.assertEqual(self.client.post('/api/v1/golden-v2/quotes',json={**body,'preview_sha256':'c'*64},headers=headers).status_code,409)
            self.assertEqual(self.client.post('/api/v1/golden-v2/quotes',json={**body,'estimated_cents':0},headers=headers).status_code,422)

    def config(self):
        path = self.root / 'private-client.env'
        path.write_text('COURSE_TRANSCRIPT_V2_OWNER_API_URL=http://127.0.0.1:8816\n'
                        'COURSE_TRANSCRIPT_V2_STATUS_TOKEN=' + self.read + '\n'
                        'COURSE_TRANSCRIPT_V2_WRITE_TOKEN=' + self.write + '\n')
        path.chmod(0o600)
        return path

    def test_private_client_config_rejects_world_readable_symlink_and_extra_keys(self):
        path = self.config()
        self.assertEqual(load_private_config(path)['COURSE_TRANSCRIPT_V2_STATUS_TOKEN'], self.read)
        path.chmod(0o644)
        with self.assertRaises(ValueError): load_private_config(path)
        path.chmod(0o600); alias = self.root / 'alias'; alias.symlink_to(path)
        with self.assertRaises(ValueError): load_private_config(alias)
        path.write_text(path.read_text() + 'UNREVIEWED_SETTING=yes\n')
        with self.assertRaises(ValueError): load_private_config(path)

    def test_owner_cli_streams_confirmed_json_without_tokens_in_output(self):
        payload = {'confirm': True, 'link': 'https://drive.google.com/drive/folders/folder0123456789'}
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"batch_id":"test"}'
        opener = unittest.mock.MagicMock(); opener.open.return_value = response
        fake_stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode()))
        output = io.StringIO()
        with patch.dict(os.environ, {'COURSE_TRANSCRIPT_V2_OWNER_API_URL':'http://127.0.0.1:8816'}), patch('sys.argv',['client','create-batch','--request-stdin']), patch('sys.stdin',fake_stdin), patch.object(client,'build_opener',return_value=opener), contextlib.redirect_stdout(output):
            self.assertEqual(client.main(), 0)
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.write)
        self.assertNotIn(self.write, output.getvalue())

    def test_owner_cli_rejects_oversized_stdin_before_http(self):
        fake_stdin = io.TextIOWrapper(io.BytesIO(b'x'*65537))
        with patch.dict(os.environ, {'COURSE_TRANSCRIPT_V2_OWNER_API_URL':'http://127.0.0.1:8816'}), patch('sys.argv',['client','create-batch','--request-stdin']), patch('sys.stdin',fake_stdin), patch.object(client,'build_opener') as opener, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): client.main()
        opener.assert_not_called()
