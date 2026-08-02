from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class ProductionHardeningRaceTests(unittest.TestCase):
    def test_delivery_failure_cannot_replace_editor_owned_state(self) -> None:
        from app.jobs.delivery_worker import _record_failure_while_locked

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            state_path = job / "drive-delivery-state.json"
            original = {
                "status": "superseded_by_editor",
                "editor_revision": 8,
                "next_attempt_at": None,
            }
            state_path.write_text(json.dumps(original), encoding="utf-8")
            _record_failure_while_locked(
                {"id": "job-1"},
                job,
                RuntimeError("simulated delivery failure"),
            )
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                original,
            )

    def test_srt_import_rejects_invalid_clock_ranges(self) -> None:
        from fastapi import HTTPException
        from app.subtitles.editor_hardened import parse_srt_strict

        for timing in (
            "00:60:00,000 --> 01:00:01,000",
            "00:00:60,000 --> 00:01:01,000",
            "00:00:00,000 --> 00:60:01,000",
            "00:00:00,000 --> 00:00:60,000",
        ):
            with self.subTest(timing=timing), self.assertRaises(HTTPException):
                parse_srt_strict(f"1\n{timing}\n字幕")


if __name__ == "__main__":
    unittest.main()
