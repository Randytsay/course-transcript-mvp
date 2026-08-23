from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.providers import correction_runtime_bridge as bridge


class CorrectionRuntimeBridgeTests(unittest.TestCase):
    def _context(self, temporary: str, mode: str = "REALTIME") -> dict:
        data_dir = Path(temporary)
        job_dir = data_dir / "jobs" / "job-1"
        job_dir.mkdir(parents=True)
        (job_dir / "subtitles.json").write_text(
            json.dumps({
                "segments": [{
                    "segment_id": "seg-0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "raw_text": "原文",
                    "text": "原文",
                }]
            }),
            encoding="utf-8",
        )
        return {
            "job_id": "job-1",
            "data_dir": str(data_dir),
            "correction_provider": "openrouter",
            "correction_provider_profile_id": "router-main",
            "correction_model": "m/x",
            "correction_execution_mode": mode,
            "correction_fallback_policy": "RAW_CHIRP_FALLBACK",
            "source_revision": "rev-1",
            "source_sha256": "sha-1",
            "segments": [{
                "segment_id": "seg-0001", "text": "原文", "raw_text": "原文",
                "start_ms": 0, "end_ms": 1000,
            }],
            "raw_segments": [{
                "segment_id": "seg-0001", "text": "原文", "raw_text": "原文",
                "start_ms": 0, "end_ms": 1000,
            }],
            "glossary": [],
        }

    def test_realtime_dispatch_returns_raw_response_for_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self._context(temporary)
            fake = type("Orchestrator", (), {
                "correct_realtime": lambda self, spec, segments, glossary: {
                    "corrections": [{"segment_id": "seg-0001", "corrected_text": "校正"}],
                    "prompt_version": "corr-v2",
                    "raw_response": "[{\"segment_id\":\"seg-0001\"}]",
                }
            })()
            with patch.object(bridge, "_build_orchestrator", return_value=fake):
                result = bridge.run_module(ctx=ctx)
            self.assertEqual(result["correction_status"], "completed_realtime")
            self.assertEqual(result["correction_raw_response"], "[{\"segment_id\":\"seg-0001\"}]")

    def test_batch_dispatch_returns_durable_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self._context(temporary, mode="BATCH")
            fake = type("Orchestrator", (), {
                "submit_batch": lambda self, spec, segments, glossary: {
                    "status": "submitted",
                    "run_id": 9,
                    "provider_job_id": "batch-9",
                    "resubmitted": True,
                }
            })()
            with patch.object(bridge, "_build_orchestrator", return_value=fake):
                result = bridge.run_module(ctx=ctx)
            self.assertEqual(result["correction_status"], "submitted")
            self.assertEqual(result["correction_run_id"], 9)
            self.assertTrue(result["lease_released"])

    def test_artifact_writer_keeps_raw_segments_and_provider_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self._context(temporary)
            raw_before = (Path(temporary) / "jobs" / "job-1" / "subtitles.json").read_bytes()
            bridge._write_corrected_outputs(
                ctx,
                {
                    **ctx,
                    "correction_status": "completed_realtime",
                    "correction_prompt_version": "corr-v2",
                    "correction_corrections": [{
                        "segment_id": "seg-0001",
                        "corrected_text": "校正",
                        "uncertain_terms": [],
                    }],
                },
                raw_response="provider-raw-response",
            )
            job_dir = Path(temporary) / "jobs" / "job-1"
            self.assertEqual((job_dir / "subtitles.json").read_bytes(), raw_before)
            for name in (
                "subtitles-corrected.json",
                "subtitles-corrected.srt",
                "subtitles-corrected.vtt",
                "review-terms.json",
                "terminology-consistency.json",
            ):
                self.assertTrue((job_dir / name).is_file(), name)
            audit = next((job_dir / "correction-v2").glob("router-realtime-*.json"))
            self.assertEqual(json.loads(audit.read_text(encoding="utf-8"))["raw_response"], "provider-raw-response")

    def test_module_entrypoint_fails_closed_without_worker_context(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "app.providers.correction_runtime_bridge"],
            cwd=Path(__file__).parents[1],
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parents[1])},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CORRECTION=FAIL", completed.stdout)


if __name__ == "__main__":
    unittest.main()
