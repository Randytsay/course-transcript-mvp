"""Read-only Golden V2 local evidence preview must fail closed."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from scripts.golden_v2_clock_fix_preview import inspect_quarantined
from tests.test_golden_v2_chirp_local_clock_fix import _repairable_result


class ClockPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "ledger.sqlite"
        ledger = GoldenV2Ledger(self.db)
        self.job_id = ledger.create_from_preflight(
            source_sha="a" * 64,mode="FLASH_CHIRP",registration_sha256="c"*64,
            estimated_stage_cents=dict(gemini_primary=1,chirp_independent=10,
                compare=0,third_optional=0,adjudication=0,golden_qa=0))
        ledger.approve(self.job_id,revision=0,approved_cents=11,
                       reference="synthetic",actor="test")
        self.win = "chunk-0000"
        self.evidence = self.root/"jobs"/self.job_id/"golden-v2"/"chirp-evidence"/self.win
        self.evidence.mkdir(parents=True)
        raw = _repairable_result()
        self.sha = hashlib.sha256(raw).hexdigest()
        (self.evidence/"provider-raw.json").write_bytes(raw)
        (self.evidence/"review-required.json").write_text(json.dumps({
            "schema":"golden-v2-chirp-window-review-v1",
            "raw_sha":self.sha,"job_id":self.job_id,
            "window_id":self.win,"operation_name":"projects/mock/operations/1",
            "source_sha":"a"*64,"actual_cents":5}))
        with sqlite3.connect(self.db) as c:
            c.execute("""INSERT INTO v2_chirp_operations (
            job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,status,
            operation_name,request_id,result_sha,actual_cents,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.job_id,self.win,"a"*64,"b"*64,"gs://mock/file",
             0,5000,"REVIEW_REQUIRED","projects/mock/operations/1","req-1",
             self.sha,5,"2026-10-15T00:00:00Z"))

    def test_safely_previews_exact_source_without_mutating_ledger(self):
        before=self.db.read_bytes()
        result=inspect_quarantined(database=self.db,artifact_root=self.root,job_id=self.job_id)
        self.assertEqual(result["reviewed_windows"],1)
        self.assertEqual(result["safe_windows"],1)
        self.assertEqual(result["results"][0]["word_count"],5)
        self.assertFalse(result["ledger_modified"])
        self.assertFalse(result["provider_calls_started"])
        self.assertEqual(self.db.read_bytes(),before)

    def test_tampered_provider_raw_never_qualifies_as_safe(self):
        (self.evidence/"provider-raw.json").write_bytes(b'{"changed":true}')
        result=inspect_quarantined(database=self.db,artifact_root=self.root,job_id=self.job_id)
        self.assertEqual(result["safe_windows"],0)
        self.assertEqual(result["requires_attention"],1)
        self.assertEqual(result["results"][0]["reason"],"EVIDENCE_BINDING_MISMATCH")


if __name__=="__main__":
    unittest.main()
