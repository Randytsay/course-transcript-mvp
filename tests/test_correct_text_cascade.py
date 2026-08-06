from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.providers import correct_text_cascade as cascade
from app.providers import correct_text_hardened as hardened


class CorrectionCascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.audit = Path(self.temp.name)
        self.audit_patch = mock.patch.object(cascade, "AUDIT_DIR", self.audit)
        self.audit_patch.start()
        self.items = [
            {
                "segment_id": "seg-1",
                "start_ms": 0,
                "end_ms": 3000,
                "raw_text": "今天介紹低溫查症候群",
            },
            {
                "segment_id": "seg-2",
                "start_ms": 3000,
                "end_ms": 6000,
                "raw_text": "這一段內容很清楚",
            },
        ]

    def tearDown(self) -> None:
        self.audit_patch.stop()
        self.temp.cleanup()

    def test_primary_result_is_used_when_safe(self) -> None:
        primary = {
            "seg-1": {
                "segment_id": "seg-1",
                "corrected_text": "今天介紹低溫差症候群",
                "uncertain_terms": [],
                "content_qa_reasons": [],
                "model": cascade.PRIMARY_MODEL,
            },
            "seg-2": {
                "segment_id": "seg-2",
                "corrected_text": "這一段內容很清楚",
                "uncertain_terms": [],
                "content_qa_reasons": [],
                "model": cascade.PRIMARY_MODEL,
            },
        }
        with mock.patch.object(cascade, "_run_model", return_value=primary) as run:
            result = cascade.correct_window(self.items, [])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["seg-1"]["correction_route"], "primary")
        self.assertEqual(result["seg-1"]["corrected_text"], "今天介紹低溫差症候群")
        self.assertFalse(result["seg-1"]["fallback_to_raw"])

    def test_only_uncertain_segment_is_escalated(self) -> None:
        primary = {
            "seg-1": {
                "segment_id": "seg-1",
                "corrected_text": "今天介紹低溫查症候群",
                "uncertain_terms": ["低溫查"],
                "content_qa_reasons": [],
                "model": cascade.PRIMARY_MODEL,
            },
            "seg-2": {
                "segment_id": "seg-2",
                "corrected_text": "這一段內容很清楚",
                "uncertain_terms": [],
                "content_qa_reasons": [],
                "model": cascade.PRIMARY_MODEL,
            },
        }
        strong = {
            "seg-1": {
                "segment_id": "seg-1",
                "corrected_text": "今天介紹低溫差症候群",
                "uncertain_terms": [],
                "content_qa_reasons": [],
                "model": cascade.ESCALATION_MODEL,
            }
        }
        with mock.patch.object(cascade, "_run_model", side_effect=[primary, strong]) as run:
            result = cascade.correct_window(self.items, [])
        self.assertEqual(run.call_count, 2)
        escalated_items = run.call_args_list[1].args[1]
        self.assertEqual([item["segment_id"] for item in escalated_items], ["seg-1"])
        self.assertEqual(result["seg-1"]["correction_route"], "escalated")
        self.assertEqual(result["seg-2"]["correction_route"], "primary")

    def test_both_model_failures_fall_back_to_chirp_raw(self) -> None:
        with mock.patch.object(
            cascade,
            "_run_model",
            side_effect=[RuntimeError("primary"), RuntimeError("strong")],
        ):
            result = cascade.correct_window(self.items, [])
        self.assertEqual(result["seg-1"]["correction_route"], "chirp_raw_fallback")
        self.assertEqual(result["seg-1"]["corrected_text"], self.items[0]["raw_text"])
        self.assertTrue(result["seg-1"]["fallback_to_raw"])

    def test_content_guard_rejects_large_rewrite(self) -> None:
        reasons = cascade.content_guard(
            "這是一段具有足夠長度而且應該忠於原文的逐字內容",
            "完全不同",
        )
        self.assertIn("excessive_deletion", reasons)

    def test_legacy_hardened_path_is_default(self) -> None:
        with mock.patch.dict(
            hardened.os.environ,
            {"CORRECTION_CASCADE_ENABLED": "false"},
        ), mock.patch.object(hardened.legacy, "main", return_value=7) as legacy_main, mock.patch.object(
            cascade,
            "main",
            side_effect=AssertionError("cascade must remain disabled"),
        ):
            self.assertEqual(hardened.main(), 7)
        legacy_main.assert_called_once_with()

    def test_feature_flag_enables_cascade(self) -> None:
        with mock.patch.dict(
            hardened.os.environ,
            {"CORRECTION_CASCADE_ENABLED": "true"},
        ), mock.patch.object(cascade, "main", return_value=9) as cascade_main, mock.patch.object(
            hardened.legacy,
            "main",
            side_effect=AssertionError("legacy path must not run"),
        ):
            self.assertEqual(hardened.main(), 9)
        cascade_main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
