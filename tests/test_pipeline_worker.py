from __future__ import annotations

import tempfile
import unittest
import json
import hashlib
import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.jobs.store import JobConflict, JobStore
from app.pipeline import worker
from app.providers import run_chirp_pipeline
from app.providers.run_chirp_pipeline import compute_chunk_plan
from app.providers import export_formats
from app.providers import patch_audible_tail
from app.providers.merge_chunks import patch_extends_timeline


class ChunkPlanTests(unittest.TestCase):
    def test_uses_ten_second_total_overlap_and_expected_boundaries(self) -> None:
        plan = compute_chunk_plan(3_350)
        self.assertEqual(
            plan,
            [
                (0, 0.0, 900.0),
                (1, 890.0, 1790.0),
                (2, 1780.0, 2680.0),
                (3, 2670.0, 3350),
            ],
        )
        boundaries = [
            round((before[2] + after[1]) / 2)
            for before, after in zip(plan, plan[1:])
        ]
        self.assertEqual(boundaries, [895, 1785, 2675])


class ChirpRecoveryTests(unittest.TestCase):
    def test_submitted_chunk_is_retained_without_a_second_submission(self) -> None:
        """A polling retry must reuse the existing paid operation."""
        with tempfile.TemporaryDirectory() as temporary:
            chunks = Path(temporary)
            manifest = chunks / "chunk-000" / "manifest.json"
            manifest.parent.mkdir()
            manifest.write_text('{"status": "SUBMITTED"}', encoding="utf-8")
            original_chunks = run_chirp_pipeline.CHUNKS
            run_chirp_pipeline.CHUNKS = chunks
            try:
                with patch.object(run_chirp_pipeline, "run_subprocess") as runner:
                    index, succeeded, detail = run_chirp_pipeline.submit_chunk(
                        0, 0.0, 900.0
                    )
            finally:
                run_chirp_pipeline.CHUNKS = original_chunks
        self.assertEqual((index, succeeded), (0, True))
        self.assertIn("existing operation retained", detail)
        runner.assert_not_called()

    def test_new_submission_uses_submit_only_mode(self) -> None:
        """Completion is recovered from GCS, never LRO polling."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="CHIRP_chunk-001=SUBMITTED", stderr=""
        )
        with tempfile.TemporaryDirectory() as temporary:
            original_chunks = run_chirp_pipeline.CHUNKS
            run_chirp_pipeline.CHUNKS = Path(temporary)
            try:
                with patch.object(
                    run_chirp_pipeline, "run_subprocess", return_value=completed
                ) as runner:
                    _, succeeded, _ = run_chirp_pipeline.submit_chunk(1, 890.0, 1790.0)
            finally:
                run_chirp_pipeline.CHUNKS = original_chunks
        self.assertTrue(succeeded)
        _, environment = runner.call_args.args
        self.assertEqual(environment["SUBMIT_ONLY"], "1")


class PipelineWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.store = JobStore(self.data / "course-transcript.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _job(self, *, approved: bool) -> dict:
        preview = self.store.create_preview(
            source_path="gdrive:課程/測試.mp3",
            source_name="測試.mp3",
            size_bytes=100,
            modified_at=None,
            mime_type="audio/mpeg",
            actor="test@example.test",
        )
        record = self.store.create_preflight_job(
            preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="test@example.test",
        )
        self.store.acquire_lease(record["id"], "preflight-test")
        estimated = self.store.record_preflight_result(
            job_id=record["id"],
            duration_seconds=120,
            source_checksum="a" * 64,
            media_format="mp3",
            audio_codec="mp3",
            estimated_cost_usd=Decimal("0.25"),
            pricing_version="test",
            worker_id="preflight-test",
        )
        if not approved:
            return estimated
        return self.store.approve_job(
            job_id=record["id"],
            expected_revision=estimated["revision"],
            confirmed_estimated_cost_usd=Decimal("0.25"),
            project_limit_usd=Decimal("200"),
            actor="test@example.test",
        )

    def test_rejects_unapproved_job_before_any_pipeline_action(self) -> None:
        record = self._job(approved=False)
        with self.assertRaises(JobConflict):
            worker.run_paid_job(
                self.store,
                record,
                data_dir=self.data,
                worker_id="pipeline-test",
            )
        self.assertEqual(
            self.store.get_job(record["id"])["status"],
            "awaiting_confirmation",
        )

    def test_approved_job_stops_at_local_human_review(self) -> None:
        record = self._job(approved=True)
        job_dir = self.data / "jobs" / record["id"]
        job_dir.mkdir(parents=True)
        source = job_dir / "source-original.mp3"
        source.write_bytes(b"test")

        def fake_module_stage(
            store: JobStore,
            item: dict,
            data_dir: Path,
            worker_id: str,
            **kwargs: object,
        ) -> None:
            stage = str(kwargs["stage"])
            store.begin_stage(
                job_id=item["id"],
                stage=stage,
                status=str(kwargs["status"]),
                detail=str(kwargs["detail"]),
                progress=int(kwargs["progress_start"]),
                input_checksum=item["source_checksum"],
                worker_id=worker_id,
            )
            store.complete_stage(
                job_id=item["id"],
                stage=stage,
                detail=f"{stage} done",
                progress=int(kwargs["progress_end"]),
                worker_id=worker_id,
            )

        with (
            patch.object(worker, "_download_source", return_value=source),
            patch.object(worker, "_normalize"),
            patch.object(worker, "_run_module_stage", side_effect=fake_module_stage),
            patch.object(worker, "_record_usage_evidence"),
            patch.object(worker, "_artifact_evidence", return_value=[]),
        ):
            finished = worker.run_paid_job(
                self.store,
                record,
                data_dir=self.data,
                worker_id="pipeline-test",
            )
        self.assertEqual(finished["status"], "awaiting_review")
        self.assertEqual(finished["progress"], 100)
        self.assertIsNone(finished["locked_by"])
        manifest = job_dir / "pipeline-manifest.json"
        self.assertTrue(manifest.exists())
        self.assertIn('"drive_upload_started": false', manifest.read_text())


class ExportTests(unittest.TestCase):
    def test_exports_docx_pdf_and_checksummed_interchange_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job_dir = Path(temporary)
            segments = [
                {
                    "segment_id": "seg-0001",
                    "start_ms": 0,
                    "end_ms": 1800,
                    "raw_text": "這是原始逐字稿。",
                    "text": "這是原始逐字稿。",
                }
            ]
            (job_dir / "subtitles.json").write_text(
                json.dumps({"segments": segments}, ensure_ascii=False),
                encoding="utf-8",
            )
            corrected = {
                "model": "gemini-3.6-flash",
                "segments": [
                    {
                        **segments[0],
                        "corrected_text": "這是校正逐字稿。",
                        "uncertain_terms": [],
                    }
                ],
            }
            (job_dir / "subtitles-corrected.json").write_text(
                json.dumps(corrected, ensure_ascii=False),
                encoding="utf-8",
            )
            for name in (
                "subtitles.srt",
                "subtitles.vtt",
                "subtitles-corrected.srt",
                "subtitles-corrected.vtt",
                "transcript-raw.txt",
                "transcript-timestamped.txt",
                "transcript-corrected.txt",
            ):
                (job_dir / name).write_text("test\n", encoding="utf-8")
            (job_dir / "join-qa.json").write_text('{"joins": []}\n')
            original_job = export_formats.JOB
            export_formats.JOB = job_dir
            try:
                self.assertEqual(export_formats.main(), 0)
            finally:
                export_formats.JOB = original_job
            for name in (
                "transcript.json",
                "transcript.csv",
                "transcript.docx",
                "transcript.pdf",
                "transcript_raw.txt",
                "transcript_corrected.txt",
                "transcript_timestamped.txt",
                "transcript.srt",
                "transcript.vtt",
                "glossary_candidates.csv",
                "glossary_decisions.yaml",
                "join_qa.json",
            ):
                self.assertGreater((job_dir / name).stat().st_size, 0)
            manifest = json.loads(
                (job_dir / "export-manifest.json").read_text(encoding="utf-8")
            )
            checksums = {
                item["name"]: item["sha256"] for item in manifest["artifacts"]
            }
            payload = (job_dir / "transcript.pdf").read_bytes()
            self.assertEqual(checksums["transcript.pdf"], hashlib.sha256(payload).hexdigest())


class TailPatchTests(unittest.TestCase):
    def test_tail_window_rechecks_last_ten_seconds_before_uncovered_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job_dir = Path(temporary)
            (job_dir / "merged-words.json").write_text(
                json.dumps({"words": [{"word": "尾", "start_ms": 59_000, "end_ms": 60_000}]}),
                encoding="utf-8",
            )
            original_job = patch_audible_tail.JOB
            try:
                patch_audible_tail.JOB = job_dir
                with patch.object(patch_audible_tail, "audio_duration_ms", return_value=65_000):
                    self.assertEqual(patch_audible_tail.tail_window(), (50_000, 65_000, 60_000))
            finally:
                patch_audible_tail.JOB = original_job

    def test_non_extending_patch_keeps_existing_tail_words(self) -> None:
        baseline = [{"word": "原", "start_ms": 60_000, "end_ms": 61_000}]
        recheck = [{"word": "重", "start_ms": 60_000, "end_ms": 60_800}]
        self.assertFalse(patch_extends_timeline(baseline, recheck))
        self.assertTrue(
            patch_extends_timeline(
                baseline,
                [{"word": "新", "start_ms": 60_500, "end_ms": 62_000}],
            )
        )


if __name__ == "__main__":
    unittest.main()
