from __future__ import annotations

import copy
import json
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.golden_v2_routes import router
from app.jobs.drive_api import DriveApiError
from app.jobs.golden_v2_batches import enqueue_link_batch, read_link_batch
from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict
from app.pipeline.golden_v2_batch_intake import run_batch_intake_pass
from app.pipeline.golden_v2_link_discovery import parse_drive_link, preview_drive_link

ROOT = 'root0123456789'
FOLDER = 'folder0123456789'
FILE = 'file0123456789'
LINK = f'https://drive.google.com/drive/folders/{FOLDER}'
COST = {s: 0 for s in STAGES}
COST['gemini_primary'] = 20
SOURCE = {'file_id': FILE, 'version': '7', 'source_path': 'gdrive:課程/第一課.mp3',
          'name': '第一課.mp3', 'size_bytes': 50, 'md5_checksum': 'a' * 32}


def preview():
    return {'preview_sha256': 'b' * 64, 'files': [copy.deepcopy(SOURCE)]}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.items = {
            FOLDER: {'id': FOLDER, 'name': '課程', 'mimeType': 'application/vnd.google-apps.folder', 'parents': [ROOT]},
            FILE: {'id': FILE, 'name': SOURCE['name'], 'mimeType': 'audio/mpeg', 'parents': [FOLDER]},
        }
        self.pages = [{'files': [self.items[FILE]], 'nextPageToken': 'page2'}, {'files': [self.items[FILE]]}]
        self.calls = []
        def api(path, params):
            self.calls.append((path, params))
            if path == '/files/root': return {'id': ROOT}
            if path.startswith('/files/'): return copy.deepcopy(self.items[path.split('/')[-1]])
            return copy.deepcopy(self.pages[1 if params.get('pageToken') else 0])
        self.api = patch('app.jobs.drive_api._api_json', side_effect=api)
        self.api.start(); self.addCleanup(self.api.stop)
        for name, result in [('_root_folder_id', ROOT), ('_resolve_path_id', FOLDER),
                             ('read_drive_media_metadata', {'id': FILE, 'version': '7', 'name': SOURCE['name'], 'size_bytes': 50, 'md5_checksum': 'a' * 32})]:
            mock = patch('app.jobs.drive_api.' + name, return_value=result)
            mock.start(); self.addCleanup(mock.stop)

    def test_parser_supports_file_folder_and_open(self):
        for url, kind in [(LINK, 'folder'), (f'https://drive.google.com/file/d/{FILE}/view?usp=sharing', 'file'),
                          (f'https://drive.google.com/open?id={FILE}', None),
                          (f'https://drive.google.com/drive/u/0/folders/{FOLDER}', 'folder')]:
            self.assertEqual(parse_drive_link(url), (FILE if kind != 'folder' else FOLDER, kind))

    def test_parser_rejects_untrusted_hosts_credentials_keys_and_ambiguous_ids(self):
        for url in [LINK.replace('https:', 'http:'), LINK.replace('drive.google.com', 'drive.google.com.evil.test'),
                    LINK.replace('drive.google.com', 'user@drive.google.com'), LINK + '#x', LINK + '?resourcekey=secret',
                    f'https://drive.google.com/open?id={FILE}&id={FOLDER}', LINK + '\n',
                    LINK.replace(FOLDER, '../bad'), LINK.replace('drive.google.com', 'drive.google.com:443')]:
            with self.subTest(url=url), self.assertRaises(ValueError): parse_drive_link(url)

    def test_paginated_preview_deduplicates_and_never_downloads(self):
        with patch('app.jobs.drive_api.download_drive_media', side_effect=AssertionError('download forbidden')):
            result = preview_drive_link(LINK)
        self.assertEqual(result['files'], [SOURCE])
        self.assertEqual(result['total_bytes'], 50)
        self.assertIsNone(result['estimated_cents'])
        self.assertFalse(result['provider_calls_started'])
        self.assertFalse(result['drive_mutation_started'])
        self.assertEqual(result['preview_sha256'], preview_drive_link(LINK)['preview_sha256'])
        self.assertEqual(len([p for p, _ in self.calls if p == '/files']), 4)

    def test_root_alias_uses_authenticated_root_id(self):
        with patch('app.jobs.drive_api._root_folder_id', return_value='root'):
            self.assertEqual(preview_drive_link(LINK)['files'], [SOURCE])

    def test_ancestry_does_not_trust_cached_browsing_path(self):
        self.items[FOLDER]['parents'] = ['outside012345']
        self.items['outside012345'] = {'id': 'outside012345', 'name': '外部', 'mimeType': 'application/vnd.google-apps.folder', 'parents': []}
        with patch('app.jobs.drive_api._ID_PATH_CACHE', {FOLDER: 'gdrive:課程'}):
            with self.assertRaises(DriveApiError): preview_drive_link(LINK)

    def test_duplicate_names_size_limits_incomplete_and_repeated_pages_fail(self):
        with self.assertRaises(DriveApiError): preview_drive_link(LINK, max_bytes=49)
        with self.assertRaises(DriveApiError): preview_drive_link(LINK, max_entries=1)
        self.pages[1]['nextPageToken'] = 'page2'
        with self.assertRaises(DriveApiError): preview_drive_link(LINK)
        self.pages[0]['incompleteSearch'] = True
        with self.assertRaises(DriveApiError): preview_drive_link(LINK)
        self.pages[0].pop('incompleteSearch')
        self.pages[0]['files'].append({**self.items[FILE], 'id': 'other0123456789'})
        with self.assertRaises(DriveApiError): preview_drive_link(LINK)

    def test_unsafe_name_shortcuts_and_file_identity_change_fail(self):
        self.items[FOLDER]['name'] = '../bad'
        with self.assertRaises(DriveApiError): preview_drive_link(LINK)
        self.items[FOLDER]['name'] = '課程'
        self.items[FILE]['mimeType'] = 'application/vnd.google-apps.shortcut'
        self.assertEqual(preview_drive_link(LINK)['files'], [])
        self.items[FILE]['mimeType'] = 'audio/mpeg'
        with patch('app.jobs.drive_api.read_drive_media_metadata', return_value={'id': 'different012345'}):
            with self.assertRaises(DriveApiError): preview_drive_link(LINK)


class BatchTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.db = self.root / 'ledger.sqlite'
        self.ledger = GoldenV2Ledger(self.db)

    def enqueue(self, **extra):
        return enqueue_link_batch(database=self.db, preview=extra.get('preview', preview()), mode='FLASH_CHIRP',
                                  estimates=extra.get('estimates', {FILE: COST}), actor='owner', ceiling_cents=1000)

    def test_repeated_batch_and_overlapping_source_are_idempotent(self):
        batch = self.enqueue()
        self.assertEqual(batch, self.enqueue())
        other = self.enqueue(preview={**preview(), 'preview_sha256': 'c' * 64})
        self.assertNotEqual(batch['batch_id'], other['batch_id'])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM v2_intake_sources').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM v2_jobs').fetchone()[0], 0)
        self.assertFalse(batch['batch_cost_approval_recorded'])
        with self.assertRaises(V2Conflict): self.enqueue(estimates={FILE: {**COST, 'gemini_primary': 30}})

    def test_unknown_batches_do_not_initialize_schema(self):
        before = self.db.read_bytes()
        self.assertIsNone(read_link_batch(self.db, 'missing'))
        self.assertEqual(before, self.db.read_bytes())

    def test_mixed_v1_database_is_rejected_before_queue_migration(self):
        with sqlite3.connect(self.db) as db: db.execute("CREATE TABLE jobs(id TEXT)")
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, "mixed or V1"): self.enqueue()
        self.assertEqual(before, self.db.read_bytes())

    def test_exact_costs_and_ceiling_required(self):
        for costs in [{}, {FILE: {}}, {FILE: {**COST, 'gemini_primary': True}}, {FILE: {**COST, 'gemini_primary': 1001}}]:
            with self.assertRaises(ValueError): self.enqueue(estimates=costs)

    def test_worker_restart_reuses_registered_job_and_never_approves(self):
        batch = self.enqueue()
        job_id = self.ledger.create_idempotent(source_sha='d' * 64, mode='FLASH_CHIRP', estimated_stage_cents=COST)
        calls = []
        def intake(**kwargs): calls.append(kwargs); return {'job_id': job_id}
        with sqlite3.connect(self.db) as db: db.execute("UPDATE v2_intake_sources SET state='PROCESSING'")
        result = run_batch_intake_pass(database=self.db, artifact_root=self.root, ceiling_cents=1000, intake=intake)
        self.assertEqual(result['action'], 'SOURCE_PREFLIGHT_COMPLETE')
        result = run_batch_intake_pass(database=self.db, artifact_root=self.root, ceiling_cents=1000, intake=intake)
        self.assertEqual(result['action'], 'INTAKE_IDLE')
        self.assertEqual(len(calls), 1)
        status = read_link_batch(self.db, batch['batch_id'])
        self.assertEqual(status['status'], 'INTAKE_COMPLETE')
        self.assertEqual(status['items'][0]['job_status'], 'AWAITING_APPROVAL')
        self.assertIsNone(self.ledger.status(job_id)['approved_cents'])

    def test_version_changed_before_registration_is_failed_and_message_redacted(self):
        batch = self.enqueue()
        def download(*args, **kwargs): return {'file_id': FILE, 'version': '8'}
        def intake(**kwargs):
            kwargs['download'](SOURCE['source_path'], self.root / 'tmp')
            self.fail('Changed bytes must never register')
        result = run_batch_intake_pass(database=self.db, artifact_root=self.root, ceiling_cents=1000, intake=intake, download=download)
        self.assertEqual(result['action'], 'SOURCE_CHANGED_OR_UNAVAILABLE')
        self.assertEqual(read_link_batch(self.db, batch['batch_id'])['status'], 'INTAKE_REVIEW_REQUIRED')
        with sqlite3.connect(self.db) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM v2_jobs').fetchone()[0], 0)

    def test_worker_lock_prevents_concurrent_source_processing(self):
        import fcntl
        self.enqueue()
        with (self.root / 'batch-intake.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_batch_intake_pass(database=self.db, artifact_root=self.root, ceiling_cents=1000,
                                          intake=lambda **kw: self.fail('must not run'))
        self.assertEqual(result['action'], 'INTAKE_BUSY')

    def test_single_file_web_intake_lock_leaves_batch_pending(self):
        from app.pipeline.golden_v2_drive_intake import SourceIntakeBusyError
        batch = self.enqueue()
        def intake(**kwargs): raise SourceIntakeBusyError("busy")
        result = run_batch_intake_pass(database=self.db, artifact_root=self.root, ceiling_cents=1000, intake=intake)
        self.assertEqual(result["action"], "INTAKE_BUSY")
        self.assertEqual(read_link_batch(self.db, batch["batch_id"])["items"][0]["intake_state"], "PENDING")

    def test_local_wav_normalizes_into_real_ledger_without_provider_calls(self):
        import hashlib
        import io
        import shutil
        import wave
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("local FFmpeg is unavailable")
        data = io.BytesIO()
        with wave.open(data, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)
        payload = data.getvalue()
        source = {**SOURCE, "size_bytes": len(payload), "md5_checksum": hashlib.md5(payload).hexdigest()}
        batch = self.enqueue(preview={**preview(), "files": [source]})
        def download(path, target, **kwargs):
            target.write_bytes(payload)
            return {"provider": "google_drive", "file_id": FILE, "version": source["version"],
                    "size_bytes": len(payload), "downloaded_sha256": hashlib.sha256(payload).hexdigest(),
                    "downloaded_md5": source["md5_checksum"], "source_path": path, "source_name": source["name"]}
        result = run_batch_intake_pass(database=self.db, artifact_root=self.root, ceiling_cents=1000, download=download)
        self.assertEqual(result["action"], "SOURCE_PREFLIGHT_COMPLETE")
        status = read_link_batch(self.db, batch["batch_id"])
        job = status["items"][0]
        self.assertEqual(job["job_status"], "AWAITING_APPROVAL")
        job_dir = self.root / "jobs" / job["job_id"]
        self.assertEqual((job_dir / "source.original").read_bytes(), payload)
        self.assertTrue((job_dir / "normalized.flac").is_file())
        self.assertFalse(json.loads((job_dir / "source-preflight.json").read_text())["provider_calls_started"])
        self.assertEqual(run_batch_intake_pass(database=self.db, artifact_root=self.root, ceiling_cents=1000, download=download)["action"], "INTAKE_IDLE")

    def test_owner_api_gates_and_fresh_preview_cas(self):
        env = {'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED': 'true', 'COURSE_TRANSCRIPT_V2_STATUS_TOKEN': 's' * 40,
               'COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED': 'true', 'COURSE_TRANSCRIPT_V2_WRITE_TOKEN': 'w' * 40,
               'COURSE_TRANSCRIPT_V2_OWNER_ACTOR': 'owner', 'COURSE_TRANSCRIPT_V2_LEDGER_PATH': str(self.db)}
        app = FastAPI(); app.include_router(router)
        client = TestClient(app)
        body = {'link': LINK, 'recursive': False}
        with patch.dict(os.environ, env), patch('app.golden_v2_routes.preview_drive_link', return_value=preview()) as discover:
            self.assertEqual(client.post('/api/v1/golden-v2/link-preview', json=body).status_code, 401)
            discover.assert_not_called()
            headers = {'Authorization': 'Bearer ' + 's' * 40}
            before = self.db.read_bytes()
            self.assertEqual(client.post('/api/v1/golden-v2/link-preview', json=body, headers=headers).status_code, 200)
            self.assertEqual(self.db.read_bytes(), before)
            create = {**body, 'preview_sha256': 'b' * 64, 'mode': 'FLASH_CHIRP', 'estimated_stage_cents_by_file': {FILE: COST}, 'confirm': True}
            self.assertEqual(client.post('/api/v1/golden-v2/link-batches', json=create, headers=headers).status_code, 401)
            write = {'Authorization': 'Bearer ' + 'w' * 40}
            self.assertEqual(client.post('/api/v1/golden-v2/link-batches', json={**create, 'confirm': False}, headers=write).status_code, 422)
            self.assertEqual(client.post('/api/v1/golden-v2/link-batches', json={**create, 'preview_sha256': 'c' * 64}, headers=write).status_code, 409)
            response = client.post('/api/v1/golden-v2/link-batches', json=create, headers=write)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(client.get('/api/v1/golden-v2/link-batches/' + response.json()['batch_id'], headers=headers).status_code, 200)
            with patch.dict(os.environ, {'COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED': 'false'}):
                self.assertEqual(client.post('/api/v1/golden-v2/link-preview', json=body, headers=headers).status_code, 404)
