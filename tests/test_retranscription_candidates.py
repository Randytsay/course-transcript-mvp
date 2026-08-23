from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.jobs.retranscription_candidates import (
    RetranscriptionCandidateStore,
    chunk_source_sha256,
)
from app.jobs.retranscription_worker import run_once
from app.jobs.store import JobStore


class RetranscriptionCandidateTests(unittest.TestCase):
    def _fixture(self, temporary: str) -> tuple[JobStore, Path, dict]:
        data_dir = Path(temporary)
        store = JobStore(data_dir / "course-transcript.db")
        now = "2026-08-23T12:00:00+00:00"
        with store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_previews(
                    id, source_path, source_name, size_bytes,
                    inspected_by, inspected_at, expires_at
                ) VALUES ('preview-1','gdrive:course.mp4','course.mp4',1000,
                          'tester',?, '2099-01-01T00:00:00+00:00')
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO jobs(
                    id, preview_id, source_path, source_name, source_size_bytes,
                    language_code, profile, enable_gemini_correction,
                    enable_subtitles, require_human_review, processing_strategy,
                    output_formats_json, status, active_stage, stage_detail,
                    created_by, created_at, updated_at, revision
                ) VALUES (
                    'job-1','preview-1','gdrive:course.mp4','course.mp4',1000,
                    'cmn-Hant-TW','highest_accuracy',1,1,1,'DYNAMIC_BATCHING',
                    '["srt","txt"]','awaiting_review','review','ready',
                    'tester',?,?,1
                )
                """,
                (now, now),
            )
        job_dir = data_dir / "jobs" / "job-1"
        chunk = job_dir / "chunks" / "chunk-000"
        chunk.mkdir(parents=True)
        (job_dir / "normalized.flac").write_bytes(b"fake-normalized-audio")
        (job_dir / "chunk-plan.json").write_text(
            json.dumps(
                {
                    "duration_seconds": 900,
                    "processing_strategy": "DYNAMIC_BATCHING",
                    "chunks": [
                        {
                            "chunk_index": 0,
                            "source_start_ms": 0,
                            "source_end_ms": 900000,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        original_manifest = {
            "chunk_index": 0,
            "role": "base",
            "source_start_ms": 0,
            "source_end_ms": 900000,
            "status": "SUCCEEDED",
            "word_count": 2,
        }
        original_words = {
            "chunk_index": 0,
            "words": [
                {"word": "原始", "start_ms": 1000, "end_ms": 1500},
                {"word": "內容", "start_ms": 1600, "end_ms": 2200},
            ],
        }
        original_partial = {
            "chunkIndex": 0,
            "sourceStartMs": 0,
            "sourceEndMs": 900000,
            "status": "SUCCEEDED",
            "wordCount": 2,
            "rawText": "原始內容",
            "firstWordMs": 1000,
            "lastWordMs": 2200,
        }
        for name, payload in (
            ("manifest.json", original_manifest),
            ("words.json", original_words),
            ("partial-transcript.json", original_partial),
        ):
            (chunk / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        record = store.get_job("job-1")
        return store, job_dir, record

    def _create(self, store: JobStore, job_dir: Path, record: dict):
        candidates = RetranscriptionCandidateStore(store)
        return candidates.create(
            job_id="job-1",
            expected_revision=int(record["revision"]),
            chunk_index=0,
            source_chunk_sha256=chunk_source_sha256(job_dir, 0),
            language_code="cmn-Hant-TW",
            processing_strategy="DYNAMIC_BATCHING",
            estimated_cost_usd=Decimal("0.0619"),
            confirmed_cost_usd=Decimal("0.0619"),
            pricing_version="test-pricing",
            actor="tester",
        )

    def test_duplicate_request_returns_same_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, job_dir, record = self._fixture(temporary)
            first, created_first = self._create(store, job_dir, record)
            second, created_second = self._create(store, job_dir, record)
            self.assertTrue(created_first)
            self.assertFalse(created_second)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(RetranscriptionCandidateStore(store).list_for_job("job-1")), 1)

    def test_submit_recover_keeps_accepted_chunk_bit_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, job_dir, record = self._fixture(temporary)
            candidate, _ = self._create(store, job_dir, record)
            accepted_chunk = job_dir / "chunks" / "chunk-000"
            before = {
                name: (accepted_chunk / name).read_bytes()
                for name in ("manifest.json", "words.json", "partial-transcript.json")
            }

            def fake_submit(module: str, env: dict[str, str], timeout_seconds: int = 900) -> int:
                self.assertEqual(module, "app.providers.chirp_chunk_hardened")
                root = (
                    Path(env["COURSE_TRANSCRIPT_DATA_DIR"])
                    / "jobs"
                    / env["JOB_NAME"]
                    / "chunks"
                    / "chunk-000"
                )
                root.mkdir(parents=True, exist_ok=True)
                (root / "audio.flac").write_bytes(b"candidate-audio")
                (root / "manifest.json").write_text(
                    json.dumps(
                        {
                            "chunk_index": 0,
                            "role": "base",
                            "source_start_ms": 0,
                            "source_end_ms": 900000,
                            "status": "SUBMITTED",
                            "operation_name": "operations/candidate-1",
                        }
                    ),
                    encoding="utf-8",
                )
                return 0

            with patch("app.jobs.retranscription_worker._run_module", side_effect=fake_submit):
                self.assertTrue(
                    run_once(store, data_dir=Path(temporary), worker_id="candidate-worker")
                )
            submitted = RetranscriptionCandidateStore(store).get(candidate["id"])
            self.assertEqual(submitted["status"], "submitted")

            def fake_recover(module: str, env: dict[str, str], timeout_seconds: int = 900) -> int:
                self.assertEqual(module, "app.providers.recover_chunk_hardened")
                root = (
                    Path(env["COURSE_TRANSCRIPT_DATA_DIR"])
                    / "jobs"
                    / env["JOB_NAME"]
                    / "chunks"
                    / "chunk-000"
                )
                manifest = {
                    "chunk_index": 0,
                    "role": "base",
                    "source_start_ms": 0,
                    "source_end_ms": 900000,
                    "status": "SUCCEEDED",
                    "operation_name": "operations/candidate-1",
                    "word_count": 4,
                }
                words = {
                    "chunk_index": 0,
                    "words": [
                        {"word": "候選", "start_ms": 900, "end_ms": 1400},
                        {"word": "辨識", "start_ms": 1500, "end_ms": 1900},
                        {"word": "更多", "start_ms": 2000, "end_ms": 2500},
                        {"word": "內容", "start_ms": 2600, "end_ms": 3100},
                    ],
                }
                partial = {
                    "chunkIndex": 0,
                    "sourceStartMs": 0,
                    "sourceEndMs": 900000,
                    "status": "SUCCEEDED",
                    "wordCount": 4,
                    "rawText": "候選辨識更多內容",
                    "firstWordMs": 900,
                    "lastWordMs": 3100,
                }
                for name, payload in (
                    ("manifest.json", manifest),
                    ("words.json", words),
                    ("partial-transcript.json", partial),
                ):
                    (root / name).write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                return 0

            with patch("app.jobs.retranscription_worker._run_module", side_effect=fake_recover):
                self.assertTrue(
                    run_once(store, data_dir=Path(temporary), worker_id="candidate-worker")
                )

            completed = RetranscriptionCandidateStore(store).get(candidate["id"])
            self.assertEqual(completed["status"], "completed")
            comparison = (
                job_dir / completed["candidate_relpath"] / "comparison.json"
            )
            self.assertTrue(comparison.is_file())
            payload = json.loads(comparison.read_text(encoding="utf-8"))
            self.assertFalse(payload["auto_apply"])
            self.assertEqual(payload["decision"], "operator_review_required")
            self.assertTrue(payload["comparison"]["text_changed"])
            for name, content in before.items():
                self.assertEqual((accepted_chunk / name).read_bytes(), content)

    def test_revision_change_marks_candidate_stale_without_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, job_dir, record = self._fixture(temporary)
            candidate, _ = self._create(store, job_dir, record)
            with store.transaction() as connection:
                connection.execute("UPDATE jobs SET revision=revision+1 WHERE id='job-1'")
            with patch("app.jobs.retranscription_worker._run_module") as provider:
                self.assertTrue(
                    run_once(store, data_dir=Path(temporary), worker_id="candidate-worker")
                )
                provider.assert_not_called()
            stale = RetranscriptionCandidateStore(store).get(candidate["id"])
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["error_kind"], "source_stale")


if __name__ == "__main__":
    unittest.main()
