from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import formal_features


class FormalTranscriptGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.jobs_dir = self.data_dir / "jobs"
        self.job_id = "formal-job"
        self.job_dir = self.jobs_dir / self.job_id
        self.job_dir.mkdir(parents=True)
        self.patch_data = patch.object(formal_features, "DATA_DIR", self.data_dir)
        self.patch_jobs = patch.object(formal_features, "JOBS_DIR", self.jobs_dir)
        self.patch_data.start()
        self.patch_jobs.start()

    def tearDown(self) -> None:
        self.patch_jobs.stop()
        self.patch_data.stop()
        self.temp.cleanup()

    def write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def prepare_chunks(self) -> None:
        self.write_json(
            self.job_dir / "chunk-plan.json",
            {
                "chunks": [
                    {"chunk_index": 0},
                    {"chunk_index": 1},
                ]
            },
        )
        for index in (0, 1):
            self.write_json(
                self.job_dir / f"chunks/chunk-{index:03d}/manifest.json",
                {"chunk_index": index, "status": "SUCCEEDED"},
            )

    def test_processing_job_is_not_formal_even_with_raw_subtitles(self) -> None:
        self.prepare_chunks()
        for name in ("merged-words.json", "subtitles.json", "qa-report.json"):
            self.write_json(self.job_dir / name, {"segments": []})
        record = {
            "status": "correcting",
            "enable_gemini_correction": True,
        }
        self.assertFalse(formal_features.formal_ready(record, self.job_dir))

    def test_gemini_job_requires_corrected_output(self) -> None:
        self.prepare_chunks()
        for name in ("merged-words.json", "subtitles.json", "qa-report.json"):
            self.write_json(self.job_dir / name, {"segments": []})
        record = {
            "status": "awaiting_review",
            "enable_gemini_correction": True,
        }
        self.assertFalse(formal_features.formal_ready(record, self.job_dir))
        self.write_json(self.job_dir / "subtitles-corrected.json", {"segments": []})
        self.assertTrue(formal_features.formal_ready(record, self.job_dir))

    def test_no_gemini_job_can_use_qa_verified_raw_segments(self) -> None:
        self.prepare_chunks()
        self.write_json(self.job_dir / "merged-words.json", {"words": [{"word": "測試"}]})
        self.write_json(
            self.job_dir / "subtitles.json",
            {
                "segments": [
                    {
                        "segment_id": "1",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "raw_text": "測試",
                    }
                ]
            },
        )
        self.write_json(self.job_dir / "qa-report.json", {"status": "PASS"})
        record = {
            "status": "awaiting_review",
            "enable_gemini_correction": False,
        }
        with patch.object(formal_features, "_job_record", return_value=record):
            result = formal_features.build_formal_segments(self.job_id)
        self.assertEqual(result["segments"][0]["corrected_text"], "測試")

    def test_incomplete_job_endpoint_returns_conflict(self) -> None:
        record = {
            "status": "transcribing",
            "enable_gemini_correction": True,
        }
        with patch.object(formal_features, "_job_record", return_value=record):
            with self.assertRaises(HTTPException) as raised:
                formal_features.build_formal_segments(self.job_id)
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
