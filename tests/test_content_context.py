from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.content_context import context_digest, normalize_content_mode, normalize_document_context
from app.jobs.store import JobStore
from app.providers.correct_text import correction_context_instruction


class ContentContextTests(unittest.TestCase):
    def test_general_context_has_no_buddhist_bias(self) -> None:
        with patch.dict(os.environ, {"CONTENT_MODE": "general", "DOCUMENT_CONTEXT": "能源績效量測驗證 M&V"}, clear=False):
            prompt = correction_context_instruction()
        self.assertIn("general recording", prompt)
        self.assertIn("能源績效量測驗證", prompt)
        self.assertNotIn("得見彌勒", prompt)

    def test_buddhist_context_uses_canonical_spelling_without_dedup_instruction(self) -> None:
        with patch.dict(os.environ, {"CONTENT_MODE": "dacheng_buddhist", "DOCUMENT_CONTEXT": ""}, clear=False):
            prompt = correction_context_instruction()
        self.assertIn("得見彌勒根本大明神咒", prompt)
        self.assertIn("Preserve every input segment", prompt)

    def test_context_validation_and_immutable_store_fields(self) -> None:
        self.assertEqual(normalize_content_mode("GENERAL"), "general")
        self.assertEqual(normalize_document_context("  課程背景 "), "課程背景")
        with self.assertRaises(ValueError):
            normalize_content_mode("unknown")
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "course-transcript.db")
            preview = store.create_preview(
                source_path="gdrive:課程/能源.mp3",
                source_name="能源.mp3",
                size_bytes=10,
                modified_at=None,
                mime_type="audio/mpeg",
                actor="operator",
            )
            job = store.create_preflight_job(
                preview_id=preview["id"], language_code="cmn-Hant-TW", profile="highest_accuracy",
                enable_gemini_correction=True, enable_subtitles=True, require_human_review=True,
                content_mode="general", document_context="能源績效量測驗證 M&V", actor="operator",
            )
            self.assertEqual(job["content_mode"], "general")
            self.assertEqual(job["document_context"], "能源績效量測驗證 M&V")
            self.assertEqual(job["context_digest"], context_digest(mode="general", document_context="能源績效量測驗證 M&V"))


if __name__ == "__main__":
    unittest.main()
