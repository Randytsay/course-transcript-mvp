"""Regression tests for fail-closed Golden completion audit."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "golden_batch_acceptance_audit.py"
SPEC = importlib.util.spec_from_file_location("golden_batch_acceptance_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GoldenAcceptanceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job_id = "sample-one"
        self.d = self.root / "jobs" / self.job_id
        self.d.mkdir(parents=True)
        self.conn = sqlite3.connect(self.root / "course-transcript.db")
        self.conn.execute(
            "CREATE TABLE stage_runs (job_id TEXT, stage TEXT, status TEXT)"
        )
        self.conn.execute("INSERT INTO stage_runs VALUES (?, 'golden_qa', 'completed')", (self.job_id,))
        self.conn.execute(
            "CREATE TABLE batches (id TEXT, completed_count INTEGER, actual_cost_usd TEXT, reserved_cost_usd TEXT)"
        )
        self.conn.execute(
            "INSERT INTO batches VALUES (?, 1, '6.0032', '1.7166')",
            (MODULE.DEFAULT_BATCH_ID,),
        )
        self.conn.execute(
            "CREATE TABLE jobs (id TEXT, batch_id TEXT, queue_position INTEGER, source_name TEXT, status TEXT)"
        )
        self.conn.execute(
            "INSERT INTO jobs VALUES (?, ?, 0, 'sample.MP3', 'completed')",
            (self.job_id, MODULE.DEFAULT_BATCH_ID),
        )
        self.conn.commit()
        self.put("chirp-completeness.json", {"status": "PASS", "blockers": []})
        self.put("qa-report.json", {"status": "PASS", "errors": [], "review_required": []})
        self.put("content-qa.json", {"status": "PASS"})
        self.put("review-audit.json", {"source_audio_sha256": "a" * 64, "independent_review_evidence": ["verified_item"]})
        self.put("golden-review-provenance.json", {"semantic_review_completed": True, "audit_file": "review-audit.json"})
        (self.d / "subtitles-cleaned.srt").write_text("subtitle")
        (self.d / "transcript-cleaned.txt").write_text("transcript")
        files = {
            ext: {"status": "completed", "sha256_match": True,
                  "remote_sha256": MODULE.sha256(self.d / name), "verified_at": "2026-10-10T00:00:00Z"}
            for ext, name in [("srt", "subtitles-cleaned.srt"), ("txt", "transcript-cleaned.txt")]
        }
        self.put("drive-publish-state.json", {"status": "completed", "files": files})
        self.put("golden-remote-checksum-proof.json", {
            "method": "independent_remote_readback", "verified_at": "2026-10-10T00:00:00Z",
            "files": {key: {"remote_sha256": record["remote_sha256"]} for key, record in files.items()}
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def put(self, name, value):
        (self.d / name).write_text(json.dumps(value), encoding="utf-8")

    def audit(self):
        return MODULE.audit_batch(self.root)

    def test_complete_with_all_explicit_evidence(self):
        audit = self.audit()
        self.assertEqual(audit["strict_completed_count"], 1)
        self.assertTrue(audit["budget_within_cap"])

    def test_review_override_without_source_evidence_is_blocked(self):
        self.put("review-audit.json", {"conclusion": "All 6 review items verified"})
        self.put("qa-report.json", {
            "status": "PASS", "review_required": [],
            "reconciled_review_items": ["audible gap with mean_volume"]
        })
        audit = self.audit()
        self.assertEqual(audit["strict_completed_count"], 0)
        self.assertIn("qa_contains_unverified_reconciliation", audit["jobs"][0]["blockers"])
        self.assertIn("semantic_review_claim_lacks_reproducible_evidence", audit["jobs"][0]["blockers"])

    def test_remote_checksum_only_previous_version_is_rejected(self):
        self.put("golden-remote-checksum-proof.json", {"method": "independent_remote_readback", "verified_at": "time", "files": {}})
        self.assertEqual(self.audit()["strict_completed_count"], 0)

    def test_inflight_stage_blocks_false_completion(self):
        self.conn.execute("INSERT INTO stage_runs VALUES (?, 'golden_qa', 'running')", (self.job_id,))
        self.conn.commit()
        self.assertIn("formal_golden_qa_stage_not_closed", self.audit()["jobs"][0]["blockers"])

    def test_job_is_awaiting_review(self):
        self.conn.execute("UPDATE jobs SET status='awaiting_review' WHERE id=?", (self.job_id,))
        self.conn.commit()
        self.assertIn("official_job_not_completed", self.audit()["jobs"][0]["blockers"])


if __name__ == "__main__":
    unittest.main()