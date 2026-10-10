"""No paid calls, unapproved QA promotion or duplicate scheduler in Golden bridge."""
import importlib.util
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "golden_guarded_dispatch.py"
SPEC = importlib.util.spec_from_file_location("golden_guarded_dispatch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GuardedDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = sqlite3.connect(self.root / "course-transcript.db")
        self.conn.execute("CREATE TABLE batches (id TEXT, actual_cost_usd TEXT, reserved_cost_usd TEXT, status TEXT, completed_count INT)")
        self.conn.execute("INSERT INTO batches VALUES (?, '6.0032','1.7166','awaiting_review',9)", (MODULE.BATCH_ID,))
        self.conn.execute("CREATE TABLE jobs (id TEXT, batch_id TEXT, queue_position INTEGER, status TEXT, active_stage TEXT, locked_by TEXT, lease_expires_at TEXT, source_name TEXT)")
        self.conn.execute("INSERT INTO jobs VALUES ('sample001', ?, 0,'awaiting_review','golden_qa',NULL,NULL,'sample.mp3')", (MODULE.BATCH_ID,))
        for n in range(1,9):
            self.conn.execute("INSERT INTO jobs VALUES (?,?,?,'awaiting_review','chirp_completeness',NULL,NULL,?)",
                              (f'sample{n+1:03}', MODULE.BATCH_ID, n, f'sample{n+1}.mp3'))
        self.conn.commit()
        job=self.root/"jobs"/"sample001"
        job.mkdir(parents=True)
        (job/"subtitles-cleaned.json").write_text("{}")
        self.put("chirp-completeness.json", {"status":"PASS", "blockers":[]})
        self.put("qa-report.json", {"status":"REVIEW","review_required":["subtitle contains repeated punctuation: seg-0001","audible subtitle gap: seg-0002->seg-0003 (5000ms)"]})
        self.put("golden-review-provenance.json", {"semantic_review_completed":False})
        self.put("drive-publish-state.json", {"status":"MISSING"})

    def put(self, filename, obj):
        (self.root/"jobs"/"sample001"/filename).write_text(json.dumps(obj))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_one_eligible(self):
        s=MODULE.scan(self.root)
        self.assertEqual(len(s["eligible"]),1)
        self.assertEqual(s["eligible"][0]["candidate_count"],1)
        self.assertEqual(len(s["hold"]),8)

    def test_claimed_completed_count_does_not_promote(self):
        self.assertEqual(MODULE.scan(self.root)["claimed_batch_completed_count"],9)
        self.assertEqual(MODULE.scan(self.root)["eligible"][0]["golden_review_count"],2)

    def test_semantic_self_attestation_blocks_revision(self):
        self.put("golden-review-provenance.json", {"semantic_review_completed":True})
        self.assertFalse(MODULE.scan(self.root)["eligible"])

    def test_lease_blocks_revision(self):
        self.conn.execute("UPDATE jobs SET locked_by='another_worker' WHERE id='sample001'")
        self.conn.commit()
        self.assertFalse(MODULE.scan(self.root)["eligible"])

    def test_budget_over_cap_blocks_revision(self):
        self.conn.execute("UPDATE batches SET reserved_cost_usd='5.0'")
        self.conn.commit()
        self.assertFalse(MODULE.scan(self.root)["eligible"])

    def test_published_job_blocks_revision(self):
        self.put("drive-publish-state.json", {"status":"completed"})
        self.assertFalse(MODULE.scan(self.root)["eligible"])

    def test_preview_does_not_apply(self):
        def mock(cmd, **kwargs):
            self.assertNotIn("--apply",cmd)
            self.assertIn("docker",cmd)
            return subprocess.CompletedProcess(cmd,0,json.dumps({
                "source_sha256":MODULE.scan(self.root)["eligible"][0]["source_sha256"],
                "changes":[{"segment_id":"seg-0001"}]}),"")
        result=MODULE.one_step(self.root,execute=False,subprocess_run=mock)
        self.assertEqual(result["outcome"],"PREVIEW_ONLY")

    def test_step_invokes_only_one_worker_apply(self):
        seen=[]
        def mock(cmd, **kwargs):
            seen.append(cmd)
            if "--apply" in cmd:
                return subprocess.CompletedProcess(cmd,0,json.dumps({
                    "changed":True,"paid_calls":0,"drive_uploads":0,
                    "old_reviews":2,"new_reviews":1,"backup":"/tmp/test-backup"}),"")
            return subprocess.CompletedProcess(cmd,0,json.dumps({
                "source_sha256":MODULE.scan(self.root)["eligible"][0]["source_sha256"],
                "changes":[{"segment_id":"seg-0001"}]}),"")
        result=MODULE.one_step(self.root,execute=True,subprocess_run=mock)
        self.assertEqual(result["outcome"],"QA_WARNING_REDUCED_NOT_GOLDEN_COMPLETE")
        self.assertEqual(len(seen),2)
        self.assertTrue(any("--apply" in s for s in seen))
        self.assertEqual(result["paid_calls"],0)


if __name__ == "__main__":
    unittest.main()