"""Golden V2 paid console API integration. Offline: no provider, no Drive write."""
import hashlib
import json
import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.golden_v2_chat_api import app
from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES
from app.pipeline.golden_v2_quote_intake import wire
from app.providers.golden_v2_audio_staging import file_sha256

SCOPE = "gdrive:Protected/Batch/"
STATUS = "status-" + "x"*38
WRITE = "write-" + "y"*39


class PaidConsoleRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root/"ledger.sqlite"
        GoldenV2Ledger(self.db)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE v2_quotes (id TEXT PRIMARY KEY,source TEXT NOT NULL,"
                         "actor TEXT NOT NULL,state TEXT NOT NULL,result TEXT,error_code TEXT)")
            conn.commit()
        env = {
            "COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED":"true",
            "COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED":"true",
            "COURSE_TRANSCRIPT_V2_PAID_API_ENABLED":"true",
            "COURSE_TRANSCRIPT_V2_STATUS_TOKEN":STATUS,
            "COURSE_TRANSCRIPT_V2_WRITE_TOKEN":WRITE,
            "COURSE_TRANSCRIPT_V2_GRANT_SECRET":"secret-"+"z"*40,
            "COURSE_TRANSCRIPT_V2_OWNER_ACTOR":"test-owner",
            "COURSE_TRANSCRIPT_V2_MAX_APPROVAL_CENTS":"500",
            "COURSE_TRANSCRIPT_V2_LEDGER_PATH":str(self.db),
            "COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED":"true",
            "COURSE_TRANSCRIPT_V2_PAID_CONSOLE_SCOPE_PREFIX":SCOPE,
        }
        p=patch.dict(os.environ,env);p.start();self.addCleanup(p.stop)
        self.client=TestClient(app)
        self.counter=0
        self.valid_metadata={}

    def job(self,seconds,estimated=101,approved=None,source_prefix=SCOPE):
        self.counter+=1; idx=self.counter
        jobid=f"v2p_{idx:032x}"
        source={"file_id":f"file{idx:012d}","source_path":source_prefix+f"lesson{idx}.mp3",
                "name":f"lesson{idx}.mp3","version":"3",
                "size_bytes":10999,"md5_checksum":"a"*32}
        self.valid_metadata[source["source_path"]]={"id":source["file_id"],
            **{k:source[k] for k in ("name","version","size_bytes","md5_checksum")}}
        quote={"job_id":jobid,"source_sha256":"c"*64,"mode":"FLASH_CHIRP",
               "estimated_cents":estimated,"duration_seconds":seconds,"drive_source":source}
        job_path=self.root/"jobs"/jobid
        job_path.mkdir(parents=True)
        path=job_path/"trusted-quote.json"
        path.write_text(wire(quote))
        digest=file_sha256(path)
        result={**quote,"quote_sha256":digest}
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO v2_quotes VALUES(?,?,?,?,?,?)",
                       (f"v2q_{idx:032x}",wire(source),"owner","COMPLETE",wire(result),None))
            db.execute("INSERT INTO v2_jobs(id,source_sha,mode,revision,approval_reference,"
                       "approved_cents,estimated_cents,status,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (jobid,"c"*64,"FLASH_CHIRP",0,None,approved,estimated,
                        "AWAITING_APPROVAL" if approved is None else "PROCESSING",
                        "2026-11-16T10:00:00Z"))
            for order,stage in enumerate(STAGES):
                db.execute("INSERT INTO v2_stages(job_id,ord,name,state,estimate_cents) "
                           "VALUES(?,?,?,?,?)",
                           (jobid,order,stage,"PENDING",
                            estimated if stage=="gemini_primary" else 0))
            db.commit()
        return jobid,digest

    def post(self,jobid,digest,amount):
        return self.client.post(f"/api/v1/golden-v2/jobs/{jobid}/authorize-paid",
            headers={"Authorization":"Bearer "+WRITE},json={
                "revision":0,"quote_sha256":digest,"source_sha256":"c"*64,
                "approved_cents":amount,"reference":"owner signed review from console",
                "confirm":True})

    def test_read_console_status_auth_and_duration(self):
        self.job(3599); self.job(3600)
        response=self.client.get("/api/v1/golden-v2/paid-console",
                                 headers={"Authorization":"Bearer "+STATUS})
        self.assertEqual(response.status_code,200,response.text)
        payload=response.json()
        self.assertEqual(payload["summary"]["eligible"],1)
        self.assertEqual(payload["summary"]["excluded_short"],1)
        self.assertEqual(payload["batch_cap_cents"],10000)
        self.assertEqual(self.client.get("/api/v1/golden-v2/paid-console").status_code,401)

    def test_signed_grant_cannot_be_issued_for_short_audio(self):
        jobid,digest=self.job(3599)
        with patch("app.pipeline.golden_v2_paid_console.drive_api.read_drive_media_metadata",
                   side_effect=self.valid_metadata.get):
            response=self.post(jobid,digest,101)
        self.assertEqual(response.status_code,409,response.text)
        self.assertFalse((self.root/"jobs"/jobid/"owner-paid-grant.json").exists())

    def test_one_hour_signed_grant_and_replay_fail_closed(self):
        jobid,digest=self.job(3600)
        with patch("app.pipeline.golden_v2_paid_console.drive_api.read_drive_media_metadata",
                   side_effect=self.valid_metadata.get):
            response=self.post(jobid,digest,101)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["status"],"QUEUED")
        envelope=json.loads((self.root/"jobs"/jobid/"owner-paid-grant.json").read_text())
        self.assertEqual(envelope["grant"]["job_id"],jobid)
        self.assertEqual(len(envelope["signature"]),64)
        with patch("app.pipeline.golden_v2_paid_console.drive_api.read_drive_media_metadata",
                   side_effect=self.valid_metadata.get):
            replay=self.post(jobid,digest,101)
        self.assertNotEqual(replay.status_code,200)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("select status from v2_jobs where id=?",(jobid,))
                             .fetchone()[0],"QUEUED")

    def test_budget_overrun_denied(self):
        for _ in range(2):
            self.job(3600,estimated=4900,approved=4900)
        jobid,digest=self.job(3600)
        with patch("app.pipeline.golden_v2_paid_console.drive_api.read_drive_media_metadata",
                   side_effect=self.valid_metadata.get):
            response=self.post(jobid,digest,201)
        self.assertEqual(response.status_code,409,response.text)
        self.assertFalse((self.root/"jobs"/jobid/"owner-paid-grant.json").exists())

    def test_legacy_auto_authorize_blocked_for_batch(self):
        jobid,_=self.job(3600)
        response=self.client.post(
            f"/api/v1/golden-v2/jobs/{jobid}/auto-budget/authorize",
            headers={"Authorization":"Bearer "+WRITE},
            json={"submitted_folder_path":SCOPE,"confirm":True})
        self.assertEqual(response.status_code,409,response.text)


if __name__ == "__main__":
    unittest.main()
