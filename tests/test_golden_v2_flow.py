"""Enforce audio-first sequencing before integrating durable paid worker."""
from __future__ import annotations

import unittest
from app.pipeline.golden_v2_flow import evaluate_recognition, QUALITY, SMART, TWO_ENGINE


class GoldenV2OrderingTests(unittest.TestCase):
    def build(self, mode: str, expected_calls: list[str], gemini: str = "男身具七寶") -> dict:
        calls: list[str] = []
        window = {"index": 0, "start_seconds": 0, "end_seconds": 30,
                  "gcs_uri": "gs://private/audio.wav", "mime_type": "audio/wav"}

        def first(win):
            self.assertEqual(win["index"], 0)
            calls.append("gemini")
            return {"status": "COMPLETE", "text": gemini,
                    "identity": {"window": {k: win[k] for k in ("index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")}}}

        def chirp():
            self.assertEqual(calls, ["gemini"])
            calls.append("chirp")
            return [{"word": "男身具七寶", "start_ms": 300, "end_ms": 2800}]

        def transcribe():
            self.assertEqual(calls, ["gemini", "chirp"])
            calls.append("third")
            return {"status": "COMPLETE", "text": "男身具七寶"}

        result = evaluate_recognition(
            mode=mode, staged_primary_windows=[window], duration_seconds=30,
            gemini_runner=first, chirp_independent_runner=chirp,
            third_independent_runner=transcribe,
        )
        self.assertEqual(calls, expected_calls)
        self.assertEqual(result["next_stage"], "CHATGPT_WEBCODEX_ADJUDICATION")
        self.assertFalse(result["golden_approved"])
        return result

    def test_quality_always_three_independent_engines(self) -> None:
        result = self.build(QUALITY, ["gemini", "chirp", "third"])
        self.assertEqual(len(result["model_order"]), 3)

    def test_smart_calls_third_only_when_disagreement(self) -> None:
        self.build(SMART, ["gemini", "chirp"])
        result = self.build(SMART, ["gemini", "chirp", "third"], gemini="男生有七寶")
        self.assertGreater(result["chirp_gemini_comparison"]["pending_review_count"], 0)

    def test_two_engine_option_does_not_call_third(self) -> None:
        self.build(TWO_ENGINE, ["gemini", "chirp"], gemini="男生有七寶")

    def test_missing_full_primary_prevents_chirp_call(self) -> None:
        calls = []
        window = {"index": 0, "start_seconds": 0, "end_seconds": 30,
                  "gcs_uri": "gs://private/audio.wav", "mime_type": "audio/wav"}
        with self.assertRaises(Exception):
            evaluate_recognition(
                mode=QUALITY, staged_primary_windows=[window],
                duration_seconds=30,
                gemini_runner=lambda w: {"status": "REQUIRES_REVIEW", "text": ""},
                chirp_independent_runner=lambda: calls.append("chirp") or [],
                third_independent_runner=lambda: calls.append("third") or {},
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
