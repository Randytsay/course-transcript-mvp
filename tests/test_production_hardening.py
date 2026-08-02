from __future__ import annotations

import inspect
import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ProductionHardeningTests(unittest.TestCase):
    def test_recovery_schedule_enforces_poll_and_backoff(self) -> None:
        from app.pipeline.recovery_schedule import is_due, schedule

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"CHIRP_RECOVERY_POLL_SECONDS": "120"}
        ):
            job = Path(temp)
            now = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
            submitted = schedule(job, "submitted", now=now)
            self.assertEqual(
                datetime.fromisoformat(submitted["next_recovery_at"]),
                now + timedelta(seconds=120),
            )
            self.assertFalse(is_due(job, now + timedelta(seconds=119)))
            self.assertTrue(is_due(job, now + timedelta(seconds=120)))
            retry = schedule(job, "retryable", now=now)
            self.assertEqual(
                datetime.fromisoformat(retry["next_recovery_at"]),
                now + timedelta(seconds=120),
            )
            retry2 = schedule(job, "retryable", now=now)
            self.assertEqual(
                datetime.fromisoformat(retry2["next_recovery_at"]),
                now + timedelta(seconds=300),
            )

    def test_completed_operation_missing_output_has_bounded_grace(self) -> None:
        from app.providers import recover_chunk_hardened as recovery
        from app.providers.hardening_common import RETRYABLE_EXIT, TERMINAL_EXIT

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"CHIRP_OUTPUT_PROPAGATION_GRACE_SECONDS": "300"},
        ):
            path = Path(temp) / "manifest.json"
            prior = {
                "operation_name": "projects/p/locations/us/operations/1",
                "submitted_at": "2026-08-01T00:00:00+00:00",
                "status": "SUBMITTED",
            }
            first = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
            with patch.object(
                recovery,
                "_operation_state",
                return_value=(True, 0, ""),
            ), patch.object(recovery, "utcnow", return_value=first):
                self.assertEqual(
                    recovery._pending_or_terminal(prior, path),
                    RETRYABLE_EXIT,
                )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                datetime.fromisoformat(persisted["operation_done_at"]),
                first,
            )
            later = first + timedelta(seconds=301)
            with patch.object(
                recovery,
                "_operation_state",
                return_value=(True, 0, ""),
            ), patch.object(recovery, "utcnow", return_value=later):
                self.assertEqual(
                    recovery._pending_or_terminal(persisted, path),
                    TERMINAL_EXIT,
                )
            terminal = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(terminal["error"]["code"], "OUTPUT_MISSING")

    def test_retained_window_requires_exact_strategy_and_offsets(self) -> None:
        from app.providers.hardening_common import window_matches

        manifest = {
            "source_start_ms": 890000,
            "source_end_ms": 1790000,
            "processing_strategy": "DYNAMIC_BATCHING",
        }
        self.assertTrue(
            window_matches(
                manifest,
                start_seconds=890,
                end_seconds=1790,
                dynamic_batching=True,
            )
        )
        self.assertFalse(
            window_matches(
                manifest,
                start_seconds=110,
                end_seconds=1010,
                dynamic_batching=True,
            )
        )
        self.assertFalse(
            window_matches(
                manifest,
                start_seconds=890,
                end_seconds=1790,
                dynamic_batching=False,
            )
        )

    def test_completed_drive_publication_makes_no_remote_request(self) -> None:
        from app.jobs.drive_publish import publish_outputs

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp) / "job"
            job.mkdir()
            (job / "drive-publish-state.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "destination": "gdrive:course",
                        "source_name": "lesson.mp3",
                        "status": "completed",
                        "files": {"srt": {"status": "completed"}},
                    }
                ),
                encoding="utf-8",
            )

            def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
                raise AssertionError(f"unexpected Drive request: {command}")

            state = publish_outputs(
                job,
                source_name="lesson.mp3",
                destination="gdrive:course",
                output_formats=["srt"],
                authorized=True,
                runner=runner,
            )
            self.assertEqual(state["status"], "completed")

    def test_delivery_worker_recognizes_editor_supersession(self) -> None:
        from app.jobs.delivery_worker import _superseded

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "drive-delivery-state.json").write_text(
                json.dumps(
                    {
                        "status": "superseded_by_editor",
                        "editor_revision": 4,
                        "next_attempt_at": None,
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(_superseded(job))

    def test_gemini_request_removes_deprecated_temperature(self) -> None:
        from app.providers.correct_text_hardened import generate_json

        source = inspect.getsource(generate_json)
        self.assertNotIn("temperature=", source)
        self.assertNotIn("top_p=", source)
        self.assertNotIn("top_k=", source)

    def test_content_guard_falls_back_only_for_severe_drift(self) -> None:
        from app.providers.correct_text_hardened import content_guard

        self.assertEqual(content_guard("彌勒菩薩摩訶薩", "彌勒菩薩摩訶薩"), [])
        self.assertEqual(content_guard("彌勒菩薩摩訶薩", "彌勒菩薩摩訶薩。"), [])
        self.assertIn(
            "excessive_deletion",
            content_guard(
                "這是一段相當長而且需要完整保留意思的逐字稿內容",
                "摘要",
            ),
        )
        self.assertIn(
            "semantic_rewrite_risk",
            content_guard(
                "今天我們接著講解彌勒大成佛經中的重要修行次第",
                "明天天氣晴朗大家一起去公園散步並準備晚餐材料",
            ),
        )

    def test_malformed_parent_gemini_response_is_persisted_before_split(self) -> None:
        from app.providers import correct_text_hardened as hardened

        items = [
            {
                "segment_id": "a",
                "start_ms": 0,
                "end_ms": 1000,
                "raw_text": "甲",
            },
            {
                "segment_id": "b",
                "start_ms": 1000,
                "end_ms": 2000,
                "raw_text": "乙",
            },
        ]
        calls = 0

        def fake_generate(prompt: str, schema: dict[str, object]):
            nonlocal calls
            calls += 1
            if calls == 1:
                text = json.dumps(
                    {
                        "segments": [
                            {
                                "segment_id": "a",
                                "corrected_text": "甲",
                                "uncertain_terms": [],
                            }
                        ]
                    }
                )
            else:
                segment_id = "a" if calls == 2 else "b"
                text = json.dumps(
                    {
                        "segments": [
                            {
                                "segment_id": segment_id,
                                "corrected_text": segment_id,
                                "uncertain_terms": [],
                            }
                        ]
                    }
                )
            return SimpleNamespace(text=text, usage_metadata=None), {
                "request_started_at": "2026-08-02T00:00:00+00:00",
                "response_completed_at": "2026-08-02T00:00:01+00:00",
                "latency_ms": 1000,
                "attempt_count": 1,
                "retry_events": [],
            }

        with tempfile.TemporaryDirectory() as temp, patch.object(
            hardened.base,
            "WORK",
            Path(temp),
        ), patch.object(
            hardened,
            "generate_json",
            side_effect=fake_generate,
        ):
            result = hardened.correct_window(items, [])
            self.assertEqual(set(result), {"a", "b"})
            audits = list(Path(temp).glob("a.split-b-*.json"))
            self.assertEqual(len(audits), 1)
            audit = json.loads(audits[0].read_text(encoding="utf-8"))
            self.assertTrue(audit["split_triggered"])
            self.assertFalse(audit["response_valid"])
            self.assertTrue(audit["raw_response"])

    def test_srt_import_rejects_partial_or_overlapping_parse(self) -> None:
        from fastapi import HTTPException
        from app.subtitles.editor_hardened import parse_srt_strict

        valid = "1\n00:00:00,000 --> 00:00:01,000\n第一段"
        segments, stats = parse_srt_strict(valid)
        self.assertEqual(len(segments), 1)
        self.assertEqual(stats["invalid_count"], 0)
        broken = valid + "\n\n2\nnot-a-time\n第二段"
        with self.assertRaises(HTTPException) as raised:
            parse_srt_strict(broken)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["invalid_count"], 1)
        overlapping = (
            valid
            + "\n\n2\n00:00:00,900 --> 00:00:02,000\n第二段"
        )
        with self.assertRaises(HTTPException):
            parse_srt_strict(overlapping)

    def test_hardened_api_installs_each_subtitle_mutation_once(self) -> None:
        from app.api_hardened import app

        paths = [str(getattr(route, "path", "")) for route in app.router.routes]
        self.assertEqual(paths.count("/api/v1/subtitles/import"), 1)
        self.assertEqual(
            paths.count("/api/v1/subtitles/{subtitle_id}/publish"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
