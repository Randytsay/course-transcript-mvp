"""No-billing Vertex Gemini 3.5 Transcribe policy regression."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from app.providers.gemini_primary_audio import PrimaryTranscriptError
from app.providers.gemini_third_transcribe import run_third_window


def window(seconds: int = 120) -> dict:
    return {"index": 0, "start_seconds": 0, "end_seconds": seconds,
            "gcs_uri": "gs://private-audio/audio.wav", "mime_type": "audio/wav"}


class ThirdEngineTests(TestCase):
    def setUp(self) -> None:
        self.client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(
                text="佛說彌勒下生經", candidates=[SimpleNamespace(finish_reason="STOP")],
                parts=[], usage_metadata={"prompt_token_count": 300},
            )
        )))

    def invoke(self, root: str, **kw: object) -> dict:
        args = dict(
            client=self.client, staged_window=window(), source_sha256="a"*64,
            job_dir=Path(root), approval_token="approved-by-worker", enabled=True,
            part_factory=lambda u, m: (u, m),
            config_factory=lambda d: d,
        )
        args.update(kw)
        return run_third_window(**args)

    def test_disabled_never_calls_provider(self) -> None:
        with TemporaryDirectory() as root:
            result = self.invoke(root, enabled=False, approval_token="")
        self.assertEqual(result["paid_calls"], 0)
        self.client.models.generate_content.assert_not_called()

    def test_15_minute_cap_fails_before_any_cost(self) -> None:
        with TemporaryDirectory() as root:
            with self.assertRaises(PrimaryTranscriptError):
                self.invoke(root, staged_window=window(901))
        self.client.models.generate_content.assert_not_called()

    def test_vocabulary_and_timestamps_not_combined(self) -> None:
        with TemporaryDirectory() as root:
            with self.assertRaises(PrimaryTranscriptError):
                self.invoke(root, custom_vocabulary=["文殊師利"], word_timestamp=True)
        self.client.models.generate_content.assert_not_called()

    def test_verbatim_uses_vertex_generate_content_configuration(self) -> None:
        with TemporaryDirectory() as root:
            first = self.invoke(root, custom_vocabulary=["文殊師利"])
            second = self.invoke(root, custom_vocabulary=["文殊師利"])
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "COMPLETE")
            self.assertEqual(first["timing_authority"], "CHIRP_3_ONLY")
            self.client.models.generate_content.assert_called_once()
            call = self.client.models.generate_content.call_args.kwargs
            self.assertEqual(call["model"], "gemini-3.5-transcribe")
            self.assertEqual(call["config"]["mode"], "VERBATIM")
            self.assertEqual(call["config"]["custom_vocabulary"], ["文殊師利"])

    def test_timeout_is_never_blindly_resubmitted(self) -> None:
        self.client.models.generate_content.side_effect = TimeoutError("network disconnected")
        with TemporaryDirectory() as root:
            with self.assertRaises(PrimaryTranscriptError):
                self.invoke(root)
            with self.assertRaises(PrimaryTranscriptError):
                self.invoke(root)
            self.client.models.generate_content.assert_called_once()


if __name__ == "__main__":
    import unittest
    unittest.main()
