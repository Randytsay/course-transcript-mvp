"""Regression coverage for router -> artifact bridge semantics."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.providers import correction_runtime_bridge as bridge
from app.providers.correction.base import ProviderError


class CorrectionWindowFallbackBridgeTests(unittest.TestCase):
    def _context(self, temporary: str) -> dict:
        data_dir = Path(temporary)
        job_dir = data_dir / "jobs" / "job-1"
        job_dir.mkdir(parents=True)
        raw_segments = [
            {
                "segment_id": "seg-0001",
                "start_ms": 0,
                "end_ms": 1000,
                "raw_text": "原文一",
                "text": "原文一",
            },
            {
                "segment_id": "seg-0002",
                "start_ms": 1000,
                "end_ms": 2000,
                "raw_text": "原文二",
                "text": "原文二",
            },
        ]
        (job_dir / "subtitles.json").write_text(
            json.dumps({"segments": raw_segments}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "job_id": "job-1",
            "data_dir": str(data_dir),
            "correction_provider": "minimax",
            "correction_provider_profile_id": "mm-main",
            "correction_model": "MiniMax-M3",
            "correction_execution_mode": "REALTIME",
            "correction_fallback_policy": "RAW_CHIRP_FALLBACK",
            "source_revision": "rev-1",
            "source_sha256": "sha-1",
            "segments": raw_segments,
            "raw_segments": raw_segments,
            "glossary": [],
        }

    def test_run_module_forwards_window_fallback_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self._context(temporary)
            fake = type("Orchestrator", (), {
                "correct_realtime": lambda self, spec, segments, glossary: {
                    "corrections": [
                        {"segment_id": "seg-0001", "corrected_text": "校正一", "uncertain_terms": []},
                        {"segment_id": "seg-0002", "corrected_text": "原文二", "uncertain_terms": []},
                    ],
                    "prompt_version": "corr-v2",
                    "raw_response": {"window_responses": {}},
                    "fallback_segment_ids": ["seg-0002"],
                    "window_results": [{
                        "window_id": "w2",
                        "status": "fallback_raw_chirp",
                        "reason": "invalid_request",
                    }],
                    "provider_circuit_opened": False,
                }
            })()
            with patch.object(bridge, "_build_orchestrator", return_value=fake):
                result = bridge.run_module(ctx=ctx)
            self.assertEqual(result["correction_fallback_segment_ids"], ["seg-0002"])
            self.assertEqual(result["correction_window_results"][0]["reason"], "invalid_request")
            self.assertFalse(result["correction_provider_circuit_opened"])

    def test_auth_failure_is_not_disguised_as_raw_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self._context(temporary)

            def fail_auth(self, spec, segments, glossary):
                raise ProviderError("auth", "invalid credential")

            fake = type("Orchestrator", (), {"correct_realtime": fail_auth})()
            with patch.object(bridge, "_build_orchestrator", return_value=fake):
                with self.assertRaises(ProviderError) as cm:
                    bridge.run_module(ctx=ctx)
            self.assertEqual(cm.exception.kind, "auth")

    def test_writer_applies_routed_text_and_marks_only_failed_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self._context(temporary)
            job_dir = Path(temporary) / "jobs" / "job-1"
            raw_before = (job_dir / "subtitles.json").read_bytes()
            result = {
                **ctx,
                "correction_status": "completed_realtime",
                "correction_prompt_version": "corr-v2",
                "correction_corrections": [
                    {"segment_id": "seg-0001", "corrected_text": "校正一", "uncertain_terms": []},
                    {"segment_id": "seg-0002", "corrected_text": "原文二", "uncertain_terms": []},
                ],
                "correction_fallback_segment_ids": ["seg-0002"],
                "correction_window_results": [{
                    "window_id": "w2",
                    "status": "fallback_raw_chirp",
                    "reason": "invalid_request",
                }],
                "correction_provider_circuit_opened": False,
            }
            bridge._write_corrected_outputs(
                ctx,
                result,
                raw_response={"window_responses": {"w1": "provider-result"}},
            )

            self.assertEqual((job_dir / "subtitles.json").read_bytes(), raw_before)
            payload = json.loads((job_dir / "subtitles-corrected.json").read_text(encoding="utf-8"))
            by_id = {item["segment_id"]: item for item in payload["segments"]}
            self.assertEqual(by_id["seg-0001"]["corrected_text"], "校正一")
            self.assertTrue(by_id["seg-0001"]["corrected"])
            self.assertFalse(by_id["seg-0001"]["correction_fallback"])
            self.assertEqual(by_id["seg-0002"]["corrected_text"], "原文二")
            self.assertTrue(by_id["seg-0002"]["correction_fallback"])
            self.assertEqual(payload["fallback_count"], 1)
            self.assertEqual(payload["fallback_segment_ids"], ["seg-0002"])
            self.assertEqual(payload["window_results"][0]["reason"], "invalid_request")

            audit = next((job_dir / "correction-v2").glob("router-realtime-*.json"))
            audit_payload = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(audit_payload["fallback_segment_ids"], ["seg-0002"])
            self.assertEqual(audit_payload["window_results"][0]["status"], "fallback_raw_chirp")


if __name__ == "__main__":
    unittest.main()
