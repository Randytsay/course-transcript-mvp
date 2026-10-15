"""Provider-free tests for the isolated V2 Gemini 3.8 primary audio adapter."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.providers import gemini_primary_audio as primary


def win(index: int, start: float, end: float, uri: str = "gs://private-bucket/window.wav") -> dict:
    return {"index": index, "start_seconds": start, "end_seconds": end,
            "gcs_uri": uri, "mime_type": "audio/wav"}


class GoldenPrimaryAudioTests(unittest.TestCase):
    def test_planner_covers_long_audio_without_gap(self) -> None:
        windows = primary.plan_windows(3700, max_seconds=1500, overlap_seconds=10)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0]["start_seconds"], 0)
        self.assertEqual(windows[-1]["end_seconds"], 3700)
        for previous, current in zip(windows, windows[1:]):
            self.assertEqual(previous["end_seconds"] - current["start_seconds"], 10)

    def test_missing_tail_or_gap_fails_closed(self) -> None:
        with self.assertRaises(primary.PrimaryTranscriptError):
            primary.validate_staged_windows([win(0, 0, 10), win(1, 11, 20)], 20)
        with self.assertRaises(primary.PrimaryTranscriptError):
            primary.validate_staged_windows([win(0, 0, 19)], 20)

    def test_rejects_unstaged_or_untrusted_audio_uri(self) -> None:
        with self.assertRaises(primary.PrimaryTranscriptError):
            primary.validate_staged_windows([win(0, 0, 10, "https://example.com/audio.wav")], 10)

    def test_no_approval_no_model_call(self) -> None:
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock()))
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(primary.PrimaryTranscriptError):
                primary.run_window(client=client, staged_window=win(0, 0, 10),
                                   source_sha256="a"*64, job_dir=Path(root),
                                   approval_token="", part_factory=lambda u, m: (u, m))
        client.models.generate_content.assert_not_called()

    def test_self_asserted_approval_token_never_authorizes_paid_call(self) -> None:
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock()))
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(primary.PrimaryTranscriptError):
                primary.run_window(
                    client=client, staged_window=win(0, 0, 10),
                    source_sha256="a"*64, job_dir=Path(root),
                    approval_token="any-string-is-not-an-approval",
                    part_factory=lambda u, m: (u, m),
                )
        client.models.generate_content.assert_not_called()

    def test_response_persisted_and_reused_without_rebilling(self) -> None:
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(
                text="復次文殊師利，男身具七寶。",
                candidates=[SimpleNamespace(finish_reason="STOP")],
                usage_metadata={"prompt_token_count": 10, "candidates_token_count": 20},
            )
        )))
        with tempfile.TemporaryDirectory() as root:
            opts = dict(client=client, staged_window=win(0, 0, 60),
                        source_sha256="a"*64, job_dir=Path(root), approval_token="authorized-job-test",
                        approval_validator=lambda t, h, model, w: t == "authorized-job-test",
                        part_factory=lambda u, m: {"uri": u, "mime": m})
            first = primary.run_window(**opts)
            second = primary.run_window(**opts)
            self.assertEqual(first, second)
            self.assertEqual(first["identity"]["model"], "gemini-3.8-flash")
            self.assertEqual(first["status"], "COMPLETE")
            client.models.generate_content.assert_called_once()
            self.assertEqual(len(list(Path(root).rglob("*.json"))), 1)
            result = primary.assemble_windows([win(0, 0, 60)], [first], 60)
            self.assertEqual(result["structural_coverage"], "PASS")
            self.assertEqual(result["acoustic_coverage"], "UNVERIFIED")
            self.assertFalse(result["golden_approved"])

    def test_context_change_never_rebills_existing_audio_window(self):
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(text="佛經續講",
                candidates=[SimpleNamespace(finish_reason="STOP")],usage_metadata={})
        )))
        with tempfile.TemporaryDirectory() as root:
            args=dict(client=client,staged_window=win(0,0,60),
                source_sha256="a"*64,job_dir=Path(root),
                approval_token="approved-test",
                approval_validator=lambda *unused: True,
                part_factory=lambda uri,mime:(uri,mime))
            first=primary.run_window(**args)
            self.assertEqual(first["status"],"COMPLETE")
            with self.assertRaisesRegex(primary.PrimaryTranscriptError,
                                        "previous attempt requires operator reconciliation"):
                primary.run_window(**args,prior_audio_context="前文不同語境")
            client.models.generate_content.assert_called_once()

    def test_prior_text_is_bounded_and_not_treated_as_audio(self):
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(text="本段的新話",
                candidates=[SimpleNamespace(finish_reason="STOP")],usage_metadata={})
        )))
        with tempfile.TemporaryDirectory() as root:
            result=primary.run_window(
                client=client,staged_window=win(1,10,70),source_sha256="a"*64,
                job_dir=Path(root),approval_token="approved-test",
                approval_validator=lambda *unused: True,
                part_factory=lambda uri,mime:(uri,mime),
                prior_audio_context=("前段話語"*200))
            sent_prompt=client.models.generate_content.call_args.kwargs["contents"][0]
            self.assertIn("前段已辨識的語境參考",sent_prompt)
            self.assertIn("不可依前文推測補寫",sent_prompt)
            self.assertLess(len(sent_prompt),1400)
            self.assertEqual(result["status"],"COMPLETE")

    def test_truncated_response_is_audited_and_never_auto_resubmitted(self) -> None:
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(
                text="只有一半", candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
                usage_metadata=None,
            )
        )))
        with tempfile.TemporaryDirectory() as root:
            opts = dict(client=client, staged_window=win(0, 0, 10),
                        source_sha256="f"*64, job_dir=Path(root), approval_token="authorized-job-test",
                        approval_validator=lambda t, h, model, w: t == "authorized-job-test",
                        part_factory=lambda u, m: (u, m))
            with self.assertRaises(primary.PrimaryTranscriptError):
                primary.run_window(**opts)
            with self.assertRaises(primary.PrimaryTranscriptError):
                primary.run_window(**opts)
            client.models.generate_content.assert_called_once()
            self.assertEqual(len(list(Path(root).rglob("*.json"))), 1)

    def test_overlap_requires_review_not_silent_text_deletion(self) -> None:
        windows = [win(0, 0, 60), win(1, 50, 90, "gs://private-bucket/window2.wav")]
        records = [
            {"status": "COMPLETE", "text": "重複的佛經與內容", "identity": {"window": {k: w[k] for k in ("index","start_seconds","end_seconds","gcs_uri","mime_type")}}}
            for w in windows
        ]
        result = primary.assemble_windows(windows, records, 90)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(result["text"].count("重複的佛經與內容"), 2)
        self.assertFalse(result["ready_for_chirp_comparison"])

    def test_reviewed_multi_seam_keeps_every_window_contribution(self) -> None:
        windows = [win(0, 0, 60), win(1, 50, 110, "gs://private-bucket/w1.wav"),
                   win(2, 100, 160, "gs://private-bucket/w2.wav")]
        texts = ["甲乙甲", "甲丙乙", "乙丁戊"]
        records = [{"status": "COMPLETE", "text": text,
                    "identity": {"window": {k: w[k] for k in (
                        "index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")}}}
                   for w, text in zip(windows, texts)]
        decisions = [
            {"between_windows": [0, 1], "left_keep_chars": 3, "right_drop_chars": 1,
             "reviewed_audio_start_ms": 50000, "reviewed_audio_end_ms": 60000,
             "audio_review_confirmed": True, "notes": "Audio checked, repeated 甲."},
            {"between_windows": [1, 2], "left_keep_chars": 3, "right_drop_chars": 1,
             "reviewed_audio_start_ms": 100000, "reviewed_audio_end_ms": 110000,
             "audio_review_confirmed": True, "notes": "Audio checked, repeated 乙."},
        ]
        review = primary.assemble_reviewed_overlaps(windows, records, 160,
                                                    seam_decisions=decisions)
        self.assertEqual(review["text"], "甲乙甲\n\n丙乙\n\n丁戊")
        self.assertTrue(review["ready_for_chirp_comparison"])
        self.assertEqual(review["acoustic_coverage"], "SEAMS_REVIEWED_WHOLE_FILE_UNVERIFIED")
        with self.assertRaises(primary.PrimaryTranscriptError):
            primary.assemble_reviewed_overlaps(windows, records, 160,
                                               seam_decisions=decisions[:1])
        with self.assertRaises(primary.PrimaryTranscriptError):
            primary.assemble_reviewed_overlaps(windows, records, 160,
                seam_decisions=[{**decisions[0], "left_keep_chars": 0}, decisions[1]])


if __name__ == "__main__":
    unittest.main()
