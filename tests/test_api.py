from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        job = self.data / "jobs" / "sample-job"
        job.mkdir(parents=True)
        (job / "source.mp3").write_bytes(b"not media")
        (job / "subtitles.json").write_text(json.dumps({"segments": [{"segment_id": "seg-1", "start_ms": 100, "end_ms": 900, "raw_text": "原文"}]}), encoding="utf-8")
        (job / "subtitles-corrected.json").write_text(json.dumps({"segments": [{"segment_id": "seg-1", "start_ms": 100, "end_ms": 900, "corrected_text": "校正後", "uncertain_terms": ["術語"]}]}), encoding="utf-8")
        (job / "qa-report.json").write_text(json.dumps({"audio": {"duration_seconds": 2}, "chirp": {"total_words": 2, "coverage_pct": 100}, "subtitles_initial": {"min_segment_ms": 800}}), encoding="utf-8")
        import app.api as api
        api.DATA_DIR = self.data
        api.JOBS_DIR = self.data / "jobs"
        self.client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(api.app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_read_only_job_api(self) -> None:
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/jobs").json()["jobs"][0]["id"], "sample-job")
        segment = self.client.get("/api/v1/jobs/sample-job/segments").json()["segments"][0]
        self.assertEqual(segment["corrected_text"], "校正後")
        self.assertEqual(segment["start_ms"], 100)
        self.assertEqual(self.client.get("/api/v1/jobs/../secrets").status_code, 404)


if __name__ == "__main__":
    unittest.main()
