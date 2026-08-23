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


class RetranscriptionStaleRecoveryTests(unittest.TestCase):
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
        accepted = job_dir / "chunks" / "chunk-000"
        accepted.mkdir(parents=True)
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
        for name, payload in (
            (
                "manifest.json",
                {
                    "chunk_index": 0,
                    "role": "base",
                    "source_start_ms": 0,
                    "source_end_ms": 900000,
                    "status": "SUCCEEDED",
                    "word_count": 2,
                },
            ),
            (
                "words.json",
                {
                    "chunk_index": 0,
                    "words": [
                        {"word": "原始", "start_ms": 1000, "end_ms": 1500},
                        {"word": "內容", "start_ms": 1600, "end_ms": 2200},
                    ],
                },
            ),
            (
                "partial-transcript.json",
                {
                    "chunkIndex": 0,
                    "sourceStartMs": 0,
                    "sourceEndMs": 900000,
                    "status": "SUCCEEDED",
                    "wordCount": 2,
                    "rawText": "原始內容",
                    "firstWordMs": 1000,
                    "lastWordMs": 2200,
                },
            ),
        ):
            (accepted / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        return store, job_dir, store.get_job("job-1")

    def _create(self, store: JobStore, job_dir: Path, record: dict) -> dict:
        row, created = RetranscriptionCandidateStore(store).create(
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
        self.assertTrue(created)
        return row

    @staticmethod
    def _write_submitted_candidate(data_dir: Path, candidate: dict) -> Path:
        chunk = (
            data_dir
            / "jobs"
            / "job-1"
            / candidate["candidate_relpath"]
            / "chunks"
            / "chunk-000"
        )
        chunk.mkdir(parents=True, exist_ok=True)
        (chunk / "audio.flac").write_bytes(b"candidate-audio")
        (chunk / "manifest.json").write_text(
            json.dumps(
                {
                    "chunk_index": 0,
                    "role": "base",
                    "source_start_ms": 0,
                    "source_end_ms": 900000,
                    "status": "SUBMITTED",
                    "operation_name": "operations/candidate-stale-1",
                }
            ),
            encoding="utf-8",
        )
        return chunk

    def test_revision_change_after_submit_recovers_then_marks_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            store, job_dir, record = self._fixture(temporary)
            candidate = self._create(store, job_dir, record)
            accepted = job_dir / "chunks" / "chunk-000"
            before = {
                name: (accepted / name).read_bytes()
                for name in ("manifest.json", "words.json", "partial-transcript.json")
            }

            def fake_submit(module: str, env: dict[str, str], timeout_seconds: int = 900) -> int:
                self.assertEqual(module, "app.providers.chirp_chunk_hardened")
                self._write_submitted_candidate(data_dir, candidate)
                return 0

            with patch("app.jobs.retranscription_worker._run_module", side_effect=fake_submit):
                self.assertTrue(run_once(store, data_dir=data_dir, worker_id="candidate-worker"))

            submitted = RetranscriptionCandidateStore(store).get(candidate["id"])
            self.assertEqual(submitted["status"], "submitted")
            self.assertTrue(submitted["submitted_at"])

            with store.transaction() as connection:
                connection.execute("UPDATE jobs SET revision=revision+1 WHERE id='job-1'")

            calls: list[str] = []

            def fake_recover(module: str, env: dict[str, str], timeout_seconds: int = 900) -> int:
                calls.append(module)
                self.assertEqual(module, "app.providers.recover_chunk_hardened")
                chunk = (
                    data_dir
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
                    "operation_name": "operations/candidate-stale-1",
                    "word_count": 3,
                    "gcs_cleanup": {"status": "completed", "deleted": ["result.json"]},
                }
                words = {
                    "chunk_index": 0,
                    "words": [
                        {"word": "候選", "start_ms": 900, "end_ms": 1400},
                        {"word": "已經", "start_ms": 1500, "end_ms": 1900},
                        {"word": "回收", "start_ms": 2000, "end_ms": 2500},
                    ],
                }
                partial = {
                    "chunkIndex": 0,
                    "sourceStartMs": 0,
                    "sourceEndMs": 900000,
                    "status": "SUCCEEDED",
                    "wordCount": 3,
                    "rawText": "候選已經回收",
                    "firstWordMs": 900,
                    "lastWordMs": 2500,
                }
                for name, payload in (
                    ("manifest.json", manifest),
                    ("words.json", words),
                    ("partial-transcript.json", partial),
                ):
                    (chunk / name).write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                return 0

            with patch("app.jobs.retranscription_worker._run_module", side_effect=fake_recover):
                self.assertTrue(run_once(store, data_dir=data_dir, worker_id="candidate-worker"))

            self.assertEqual(calls, ["app.providers.recover_chunk_hardened"])
            stale = RetranscriptionCandidateStore(store).get(candidate["id"])
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["error_kind"], "source_stale")
            candidate_dir = job_dir / stale["candidate_relpath"]
            self.assertFalse((candidate_dir / "comparison.json").exists())
            evidence = json.loads(
                (candidate_dir / "stale-result.json").read_text(encoding="utf-8")
            )
            self.assertTrue(evidence["provider_result_preserved"])
            self.assertEqual(evidence["gcs_cleanup"]["status"], "completed")
            self.assertFalse(evidence["accepted_artifacts_mutated"])
            for name, content in before.items():
                self.assertEqual((accepted / name).read_bytes(), content)

            with patch("app.jobs.retranscription_worker._run_module") as provider:
                self.assertFalse(run_once(store, data_dir=data_dir, worker_id="candidate-worker"))
                provider.assert_not_called()

    def test_queued_db_with_retained_submit_manifest_is_not_resubmitted_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            store, job_dir, record = self._fixture(temporary)
            candidate = self._create(store, job_dir, record)
            self._write_submitted_candidate(data_dir, candidate)
            with store.transaction() as connection:
                connection.execute("UPDATE jobs SET revision=revision+1 WHERE id='job-1'")

            with patch("app.jobs.retranscription_worker._run_module") as provider:
                self.assertTrue(run_once(store, data_dir=data_dir, worker_id="candidate-worker"))
                provider.assert_not_called()

            recovered_state = RetranscriptionCandidateStore(store).get(candidate["id"])
            self.assertEqual(recovered_state["status"], "submitted")
            self.assertEqual(recovered_state["operation_name"], "operations/candidate-stale-1")
            self.assertTrue(recovered_state["submitted_at"])


if __name__ == "__main__":
    unittest.main()
