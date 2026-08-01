from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import live_features
from app.api_ext import (
    CreateBatchWithParallelismRequest,
    _validate_parallelism,
)


class ParallelismValidationTests(unittest.TestCase):
    def _payload(self, value: object | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "batch_preview_id": "a" * 32,
            "language_code": "cmn-Hant-TW",
            "profile": "highest_accuracy",
            "enable_gemini_correction": True,
            "enable_subtitles": True,
            "require_human_review": True,
        }
        if value is not None:
            payload["chirp_max_parallel_chunks"] = value
        return payload

    def test_default_and_allowed_values(self) -> None:
        default = CreateBatchWithParallelismRequest.model_validate(self._payload())
        self.assertEqual(default.chirp_max_parallel_chunks, 3)
        self.assertEqual(default.output_formats, ["srt", "txt", "csv"])
        for value in range(1, 6):
            parsed = CreateBatchWithParallelismRequest.model_validate(
                self._payload(value)
            )
            self.assertEqual(parsed.chirp_max_parallel_chunks, value)

    def test_output_formats_are_forward_compatible_input(self) -> None:
        payload = self._payload()
        payload["output_formats"] = ["srt", "pdf"]
        self.assertEqual(
            CreateBatchWithParallelismRequest.model_validate(payload).output_formats,
            ["srt", "pdf"],
        )

    def test_invalid_json_types_are_rejected(self) -> None:
        for value in (0, -1, "3", 2.5):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                CreateBatchWithParallelismRequest.model_validate(self._payload(value))

    def test_server_limit_is_authoritative(self) -> None:
        with patch.dict(os.environ, {"CHIRP_MAX_PARALLEL_CHUNKS_LIMIT": "4"}):
            self.assertEqual(_validate_parallelism(4), 4)
            with self.assertRaises(HTTPException) as raised:
                _validate_parallelism(5)
            self.assertEqual(raised.exception.status_code, 422)


class LiveChunkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.jobs_dir = self.data_dir / "jobs"
        self.job_id = "sample-job"
        self.job_dir = self.jobs_dir / self.job_id
        self.job_dir.mkdir(parents=True)
        self.patch_data = patch.object(live_features, "DATA_DIR", self.data_dir)
        self.patch_jobs = patch.object(live_features, "JOBS_DIR", self.jobs_dir)
        self.patch_record = patch.object(
            live_features,
            "_job_record",
            return_value={
                "id": self.job_id,
                "status": "transcribing",
                "updated_at": "2026-08-01T00:00:00+00:00",
                "chirp_max_parallel_chunks": 3,
                "estimated_cost_usd": "2.00",
            },
        )
        self.patch_data.start()
        self.patch_jobs.start()
        self.patch_record.start()

    def tearDown(self) -> None:
        self.patch_record.stop()
        self.patch_jobs.stop()
        self.patch_data.stop()
        self.temp.cleanup()

    def write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def create_plan(self) -> None:
        self.write_json(
            self.job_dir / "chunk-plan.json",
            {
                "chunks": [
                    {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 900000},
                    {"chunk_index": 1, "source_start_ms": 890000, "source_end_ms": 1790000},
                    {"chunk_index": 2, "source_start_ms": 1780000, "source_end_ms": 2680000},
                    {"chunk_index": 3, "source_start_ms": 2670000, "source_end_ms": 3349000},
                ]
            },
        )

    def test_plan_drives_total_and_waiting_states(self) -> None:
        self.create_plan()
        self.write_json(
            self.job_dir / "chunks/chunk-000/manifest.json",
            {
                "chunk_index": 0,
                "source_start_ms": 0,
                "source_end_ms": 900000,
                "status": "RUNNING",
            },
        )
        self.write_json(
            self.job_dir / "chunks/chunk-001/manifest.json",
            {
                "chunk_index": 1,
                "source_start_ms": 890000,
                "source_end_ms": 1790000,
                "status": "SUCCEEDED",
                "word_count": 4,
                "created_at": "2026-08-01T00:05:00+00:00",
            },
        )
        self.write_json(
            self.job_dir / "chunks/chunk-001/words.json",
            {
                "words": [
                    {"word": "美安", "start_ms": 890100, "end_ms": 890300},
                    {"word": "OPC", "start_ms": 890310, "end_ms": 890500},
                    {"word": "3", "start_ms": 890510, "end_ms": 890600},
                    {"word": "。", "start_ms": 890610, "end_ms": 890650},
                ]
            },
        )

        result = live_features.build_chunk_progress(self.job_id)

        self.assertEqual(result["totalCount"], 4)
        self.assertEqual(result["completedCount"], 1)
        self.assertEqual(
            [item["status"] for item in result["chunks"]],
            ["RUNNING", "SUCCEEDED", "WAITING", "WAITING"],
        )
        self.assertTrue(result["chunks"][1]["hasTranscript"])
        partial = json.loads(
            (self.job_dir / "chunks/chunk-001/partial-transcript.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(partial["rawText"], "美安 OPC 3。")

    def test_unfinished_chunk_cannot_serve_stale_partial(self) -> None:
        self.create_plan()
        self.write_json(
            self.job_dir / "chunks/chunk-000/manifest.json",
            {"chunk_index": 0, "status": "RUNNING"},
        )
        self.write_json(
            self.job_dir / "chunks/chunk-000/partial-transcript.json",
            {"rawText": "stale"},
        )
        with self.assertRaises(HTTPException) as raised:
            live_features.get_chunk_transcript(self.job_id, 0)
        self.assertEqual(raised.exception.status_code, 409)

    def test_error_is_redacted(self) -> None:
        value = live_features.safe_chunk_error(
            {
                "code": 7,
                "message": (
                    "authorization=secret gs://private-bucket/object "
                    "projects/p/locations/us/operations/123 /run/secrets/key.json"
                ),
            }
        )
        self.assertIsNotNone(value)
        self.assertNotIn("secret", value or "")
        self.assertNotIn("private-bucket", value or "")
        self.assertNotIn("operations/123", value or "")
        self.assertNotIn("/run/secrets", value or "")

    def test_live_cost_counts_actual_submitted_chunk_durations_once(self) -> None:
        self.write_json(
            self.job_dir / "chunk-plan.json",
            {
                "chunks": [
                    {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 900000},
                    {"chunk_index": 1, "source_start_ms": 890000, "source_end_ms": 1790000},
                ]
            },
        )
        for index, status in ((0, "SUCCEEDED"), (1, "SUBMITTED")):
            self.write_json(
                self.job_dir / f"chunks/chunk-{index:03d}/manifest.json",
                {"chunk_index": index, "status": status},
            )
        self.write_json(
            self.job_dir / "glossary/global-terms.json",
            {
                "usage_metadata": {
                    "prompt_token_count": 1000,
                    "candidates_token_count": 200,
                }
            },
        )
        self.write_json(
            self.job_dir / "correction-v2/window-000.json",
            {
                "usage_metadata": {
                    "promptTokenCount": 2000,
                    "candidatesTokenCount": 300,
                }
            },
        )

        with patch.dict(
            os.environ,
            {
                "COURSE_TRANSCRIPT_CHIRP_USD_PER_MINUTE": "0.016",
                "COURSE_TRANSCRIPT_GEMINI_INPUT_USD_PER_MILLION": "1.50",
                "COURSE_TRANSCRIPT_GEMINI_OUTPUT_USD_PER_MILLION": "7.50",
            },
        ):
            result = live_features.build_live_cost(self.job_id)

        self.assertEqual(result["submittedChunkCount"], 2)
        self.assertEqual(result["completedChunkCount"], 1)
        self.assertEqual(Decimal(result["chirpEstimatedUsd"]), Decimal("0.48"))
        self.assertGreater(Decimal(result["geminiEstimatedUsd"]), Decimal("0"))


if __name__ == "__main__":
    unittest.main()
