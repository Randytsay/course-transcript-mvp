"""No-cost unit tests for authenticated Golden V2 paid-console policy."""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict
from app.pipeline.golden_v2_paid_console import (
    MIN_DURATION_SECONDS, check_paid_approval, owner_batch_lock,
    scope_prefix, snapshot,
)

PREFIX = "gdrive:Protected/Batch/"


class PaidConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "ledger.sqlite"
        GoldenV2Ledger(self.db)
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE v2_quotes (id TEXT PRIMARY KEY, source TEXT NOT NULL, "
                      "actor TEXT NOT NULL, state TEXT NOT NULL, result TEXT, error_code TEXT)")
        self.counter = 0

    def add(self, duration=3600, quote_cents=110, approved=None,
            status="AWAITING_APPROVAL", prefix=PREFIX, state="COMPLETE"):
        self.counter += 1
        idx = self.counter
        job_id = f"v2p_{idx:032x}"
        source = {"file_id": f"file{idx:010d}", "name": f"lesson{idx}.mp3",
                  "source_path": f"{prefix}lesson{idx}.mp3",
                  "size_bytes": 1_000_000, "version": "7", "md5_checksum": "a"*32}
        quote = {"job_id": job_id, "duration_seconds": duration, "estimated_cents": quote_cents,
                 "source_sha256": "b"*64, "quote_sha256": "c"*64, "drive_source": source}
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO v2_quotes VALUES (?,?,?,?,?,?)",
                      (f"v2q_{idx:032x}",json.dumps(source), "owner", state,
                       json.dumps(quote), None))
            c.execute("INSERT INTO v2_jobs "
                      "(id,source_sha,mode,revision,approval_reference,approved_cents,"
                      "estimated_cents,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (job_id, "b"*64, "FLASH_CHIRP", int(approved is not None),
                       "owner" if approved is not None else None,
                       approved, quote_cents, status, "2026-11-16T10:00:00Z"))
            for order, name in enumerate(STAGES):
                c.execute("INSERT INTO v2_stages(job_id,ord,name,state,estimate_cents)"
                          " VALUES(?,?,?,?,?)",
                          (job_id,order,name,"PENDING",
                           quote_cents if name=="gemini_primary" else 0))
        return job_id, source

    def check(self, job_id, amount=110):
        return check_paid_approval(self.db, PREFIX, job_id, amount,"b"*64,"c"*64)

    def test_default_denied_and_malformed_scopes_fail(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(scope_prefix())
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED":"true",
                                     "COURSE_TRANSCRIPT_V2_PAID_CONSOLE_SCOPE_PREFIX":"../"},clear=True):
            with self.assertRaises(V2Conflict):
                scope_prefix()
        with patch.dict(os.environ, {"COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED":"true",
                                     "COURSE_TRANSCRIPT_V2_PAID_CONSOLE_SCOPE_PREFIX":PREFIX},clear=True):
            self.assertEqual(scope_prefix(),PREFIX)

    def test_duration_boundary_and_immutable_version(self):
        short,_=self.add(duration=3599.999)
        long,source=self.add(duration=3600)
        result=snapshot(self.db,PREFIX)
        self.assertEqual(result["summary"]["eligible"],1)
        self.assertEqual(result["summary"]["excluded_short"],1)
        self.assertFalse(next(x for x in result["jobs"] if x["job_id"]==short)["can_approve"])
        with patch("app.pipeline.golden_v2_paid_console.drive_api.read_drive_media_metadata") as read:
            read.return_value={"id":source["file_id"], **{k:source[k]
                         for k in ("version","name","size_bytes","md5_checksum")}}
            with self.assertRaisesRegex(V2Conflict,"below 3600"):
                self.check(short)
            with owner_batch_lock(self.db.parent):
                self.assertEqual(self.check(long)["reserved_after_cents"],110)
            read.return_value["version"]="8"
            with self.assertRaisesRegex(V2Conflict,"source changed"):
                self.check(long)

    def test_cumulative_budget_includes_old_approvals(self):
        self.add(duration=80,quote_cents=43,approved=43,status="PROCESSING")
        self.add(duration=3500,quote_cents=41,approved=41,status="PROCESSING")
        target,source=self.add(duration=3650,quote_cents=110)
        with patch("app.pipeline.golden_v2_paid_console.drive_api.read_drive_media_metadata",
                   return_value={"id":source["file_id"],**{k:source[k]
                                 for k in ("version","name","size_bytes","md5_checksum")}}):
            self.assertEqual(snapshot(self.db,PREFIX)["reserved_exposure_cents"],84)
            self.assertEqual(self.check(target,110)["reserved_after_cents"],194)
            with self.assertRaisesRegex(V2Conflict,"ceiling"):
                self.check(target,10000)

    def test_incomplete_or_unmatched_quote_never_eligible(self):
        pending,_=self.add(duration=3600,state="PENDING")
        foreign,_=self.add(duration=4000,prefix="gdrive:Other/")
        self.assertEqual(snapshot(self.db,PREFIX)["summary"]["pending_quote"],1)
        with self.assertRaisesRegex(V2Conflict,"incomplete"):
            self.check(pending)
        with self.assertRaisesRegex(V2Conflict,"eligible member"):
            self.check(foreign)

    def test_quote_hash_mismatch_and_no_external_calls(self):
        target,_=self.add(duration=3700)
        with self.assertRaisesRegex(V2Conflict,"differs"):
            check_paid_approval(self.db,PREFIX,target,110,"b"*64,"d"*64)


if __name__ == "__main__":
    unittest.main()
