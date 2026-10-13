from datetime import date
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_pricing import quote_duration, gemini_usage_cents, chirp_cents
from app.pipeline.golden_v2_quote_intake import enqueue_quote, read_quote, run_quote_intake_pass


class TrustedQuoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.db = self.root/'ledger.sqlite'
        GoldenV2Ledger(self.db)
        self.source = dict(file_id='drive-file-12345',version='7',source_path='gdrive:folder/a.MP3',
                           size_bytes=3,md5_checksum='abc',name='a.MP3')
        self.preview = {'files':[self.source]}
        self.enqueue = lambda: enqueue_quote(database=self.db,preview=self.preview,
                                              file_id=self.source['file_id'],actor='owner')

    def quote(self, seconds):
        return quote_duration(seconds,today=date(2026,10,14))

    def test_quote_caps_account_for_every_overlapping_request_and_expire(self):
        short = self.quote(240); long = self.quote(9000)
        self.assertGreater(long['estimated_cents'],short['estimated_cents'])
        self.assertEqual(long['primary_window_count'],4)
        self.assertEqual(short['estimated_stage_cents']['third_optional'],0)
        self.assertFalse(short['invoice_verified'])
        self.assertFalse(short['storage_and_network_included'])
        with self.assertRaises(ValueError): quote_duration(240,today=date(2027,1,1))
        for duration in [True,0,float('inf'),43201]:
            with self.assertRaises(ValueError): self.quote(duration)

    def test_usage_is_not_estimate_and_requires_actual_complete_metadata(self):
        self.assertEqual(gemini_usage_cents([{'usage_metadata':dict(prompt_token_count=1000,candidates_token_count=100,thoughts_token_count=100)}]),1)
        for usage in [{},{'prompt_token_count':1}, {'prompt_token_count':True,'candidates_token_count':2}]:
            with self.assertRaises(ValueError): gemini_usage_cents([{'usage_metadata':usage}])
        self.assertEqual(chirp_cents(60),1)
        self.assertEqual(chirp_cents(1200),6)

    def test_enqueue_deduplicates_and_has_no_job_or_approval(self):
        first = self.enqueue(); self.assertEqual(first,self.enqueue())
        self.assertEqual(first['state'],'PENDING')
        with self.assertRaises(ValueError): enqueue_quote(database=self.db,preview=self.preview,file_id='other',actor='owner')
        self.assertIsNone(read_quote(self.db,'missing'))

    def download(self, path, media, **kwargs):
        media.write_bytes(b'abc')
        return dict(file_id=self.source['file_id'],version='7',size_bytes=3,
                    downloaded_md5='abc',source_path=path)

    def test_changed_source_never_registers_and_retains_safe_error(self):
        row = self.enqueue()
        def changed(*args,**kwargs):
            value = self.download(*args,**kwargs); value['version']='8'; return value
        with patch('app.pipeline.golden_v2_quote_intake.register_source_preflight') as register:
            run_quote_intake_pass(database=self.db,artifact_root=self.root,ceiling_cents=1000,
                                 download=changed,probe=lambda p:240,price=self.quote,register=register)
        register.assert_not_called()
        result = read_quote(self.db,row['quote_id'])
        self.assertEqual(result['state'],'FAILED')
        self.assertEqual(result['error_code'],'QUOTE_DOWNLOAD_FAILED')
        retry=enqueue_quote(database=self.db,preview=self.preview,file_id=self.source['file_id'],actor='owner',retry_failed=True)
        self.assertEqual(retry['state'],'PENDING')
        import sqlite3
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT outcome FROM v2_quote_attempts').fetchone()[0],'QUOTE_DOWNLOAD_FAILED')

    def test_over_cap_is_a_real_quote_without_job_registration(self):
        row = self.enqueue()
        with patch('app.pipeline.golden_v2_quote_intake.register_source_preflight') as register:
            run_quote_intake_pass(database=self.db,artifact_root=self.root,ceiling_cents=1,
                                 download=self.download,probe=lambda p:240,price=self.quote,register=register)
        register.assert_not_called()
        result = read_quote(self.db,row['quote_id'])
        self.assertEqual(result['state'],'OVER_CAP')
        self.assertGreater(result['result']['estimated_cents'],1)

    def test_process_resume_retains_exact_immutable_quote_and_does_not_redownload(self):
        row = self.enqueue()
        job_id = 'v2p_'+'a'*32
        def register(**kwargs):
            path=self.root/'jobs'/job_id; path.mkdir(parents=True)
            return {'job_id':job_id,'source_sha256':'f'*64}
        run_quote_intake_pass(database=self.db,artifact_root=self.root,ceiling_cents=1000,
                             download=self.download,probe=lambda p:240,price=self.quote,register=register)
        result=read_quote(self.db,row['quote_id'])
        self.assertEqual(result['state'],'COMPLETE')
        self.assertEqual(result['result']['job_id'],job_id)
        self.assertTrue((self.root/'jobs'/job_id/'trusted-quote.json').is_file())
        with patch('app.pipeline.golden_v2_quote_intake.download_drive_media') as download:
            self.assertEqual(run_quote_intake_pass(database=self.db,artifact_root=self.root,ceiling_cents=1000,download=download)['action'],'QUOTE_INTAKE_IDLE')
        download.assert_not_called()
