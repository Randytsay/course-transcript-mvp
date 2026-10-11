"""Provider-free tests for the proposed V2 Gemini audio-first adapter."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.providers.gemini_audio_primary_v2 import (
    AudioChunk,
    assemble_primary_candidates,
    plan_audio_chunks,
    primary_prompt,
    transcribe_audio_chunk,
)

DIGEST = "a" * 64


class GeminiAudioPrimaryV2Tests(unittest.TestCase):
    def test_short_audio_keeps_whole_context(self) -> None:
        chunks = plan_audio_chunks(3300000)
        self.assertEqual(chunks, [AudioChunk(0, 0, 3300000)])

    def test_long_audio_chunks_have_overlap_without_missing_tail(self) -> None:
        spans = plan_audio_chunks(7500000, max_chunk_ms=3600000, overlap_ms=10000)
        self.assertEqual(spans[0], AudioChunk(0, 0, 3600000))
        self.assertEqual(spans[1].start_ms, 3590000)
        self.assertEqual(spans[-1].end_ms, 7500000)
        self.assertTrue(all(x.duration_ms > 0 for x in spans))

    def test_bad_overlap_or_duration_is_rejected(self) -> None:
        for duration, chunk, overlap in ((0, 100, 5), (100, 0, 0), (100, 100, 100)):
            with self.assertRaises(ValueError):
                plan_audio_chunks(duration, max_chunk_ms=chunk, overlap_ms=overlap)

    def test_reference_never_authorizes_invented_scripture(self) -> None:
        instruction = primary_prompt(content_mode="dacheng_buddhist", reference_text="復次文殊師利")
        self.assertIn("不可", instruction)
        self.assertIn("參考", instruction)
        self.assertIn("復次文殊師利", instruction)
        with self.assertRaises(ValueError):
            primary_prompt(reference_text="x" * 12001)

    def test_paid_call_denied_without_approval(self) -> None:
        client = SimpleNamespace(models=SimpleNamespace(generate_content=mock.Mock()))
        with self.assertRaises(PermissionError):
            transcribe_audio_chunk(
                client, gcs_uri="gs://private/chunk.wav", mime_type="audio/wav",
                source_sha256=DIGEST, chunk=AudioChunk(0, 0, 10000),
            )
        client.models.generate_content.assert_not_called()

    def test_approved_mock_call_is_unverified_not_golden(self) -> None:
        response = SimpleNamespace(
            text="復次文殊師利。男身具七寶。",
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
            usage_metadata=None,
        )
        client = SimpleNamespace(models=SimpleNamespace(generate_content=mock.Mock(return_value=response)))
        item = transcribe_audio_chunk(
            client, gcs_uri="gs://private/chunk.wav", mime_type="audio/wav",
            source_sha256=DIGEST, chunk=AudioChunk(0, 0, 10000),
            approval_reference="MOCK_ONLY-NO-BILLING",
            content_mode="dacheng_buddhist",
        )
        self.assertTrue(item["candidate_valid"])
        self.assertTrue(item["needs_acoustic_validation"])
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.8-flash")
        self.assertIsNone(request["config"].temperature)
        self.assertEqual(request["config"].thinking_config.thinking_level.name, "MEDIUM")

    def test_max_output_tokens_is_not_complete(self) -> None:
        response = SimpleNamespace(
            text="這是截斷的稿",
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))],
            usage_metadata=None,
        )
        client = SimpleNamespace(models=SimpleNamespace(generate_content=mock.Mock(return_value=response)))
        item = transcribe_audio_chunk(
            client, gcs_uri="gs://private/chunk.wav", mime_type="audio/wav",
            source_sha256=DIGEST, chunk=AudioChunk(0, 0, 10000),
            approval_reference="MOCK_ONLY-NO-BILLING",
        )
        self.assertFalse(item["candidate_valid"])

    def test_overlap_merge_with_exact_match(self) -> None:
        expected = [AudioChunk(0, 0, 60000), AudioChunk(1, 50000, 110000)]
        pieces = [
            {"chunk_index": 0, "start_ms": 0, "end_ms": 60000,
             "candidate_valid": True, "text": "第一段音訊內容十個字重疊"},
            {"chunk_index": 1, "start_ms": 50000, "end_ms": 110000,
             "candidate_valid": True, "text": "十個字重疊後面還有語音"},
        ]
        out = assemble_primary_candidates(pieces, expected=expected)
        self.assertTrue(out["primary_candidate_complete"])
        self.assertFalse(out["golden_eligible"])
        self.assertEqual(out["text"].count("十個字重疊"), 1)

    def test_disagreement_in_overlap_requires_review(self) -> None:
        expected = [AudioChunk(0, 0, 60000), AudioChunk(1, 50000, 110000)]
        out = assemble_primary_candidates([
            {"chunk_index": 0, "start_ms": 0, "end_ms": 60000,
             "candidate_valid": True, "text": "這是第一段內容"},
            {"chunk_index": 1, "start_ms": 50000, "end_ms": 110000,
             "candidate_valid": True, "text": "其他話題和句子"},
        ], expected=expected)
        self.assertFalse(out["primary_candidate_complete"])
        self.assertEqual(out["issues"][0]["reason"], "ambiguous_overlap")


if __name__ == "__main__":
    unittest.main()
