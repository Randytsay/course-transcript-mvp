"""Provider-free Gemini 3.5 Transcribe argument/SDK tests."""
import unittest
from types import SimpleNamespace
from unittest import mock

from app.providers.gemini_audio_primary_v2 import AudioChunk
from app.providers.gemini_transcribe_v2 import transcribe_independent_chunk

DIGEST = "b" * 64


def invoke(client, **changes):
    args = {
        "client": client, "gcs_uri": "gs://private/chunk.wav",
        "mime_type": "audio/wav", "source_sha256": DIGEST,
        "chunk": AudioChunk(0, 0, 60000), "approval_reference": "MOCK-ONLY",
    }
    args.update(changes)
    return transcribe_independent_chunk(**args)


class TranscribeAdapterTests(unittest.TestCase):
    def setUp(self):
        response = SimpleNamespace(
            text="復次文殊師利，男身具七寶。",
            parts=[], usage_metadata=None,
        )
        self.client = SimpleNamespace(models=SimpleNamespace(generate_content=mock.Mock(return_value=response)))

    def test_no_approval_no_paid_request(self):
        with self.assertRaises(PermissionError):
            invoke(self.client, approval_reference=None)
        self.client.models.generate_content.assert_not_called()

    def test_default_is_verbatim_and_not_timing_authority(self):
        result = invoke(self.client)
        call = self.client.models.generate_content.call_args.kwargs
        self.assertEqual(call["model"], "gemini-3.5-transcribe")
        self.assertEqual(call["config"].audio_transcription_config.mode, "VERBATIM")
        self.assertTrue(result["candidate_valid"])
        self.assertTrue(result["never_authoritative_timing"])

    def test_vocabulary_can_be_combined_with_diarization_as_in_vertex_docs(self):
        invoke(self.client, custom_vocabulary=["文殊師利"], diarization=True)
        config = self.client.models.generate_content.call_args.kwargs["config"].audio_transcription_config
        self.assertEqual(config.custom_vocabulary, ["文殊師利"])
        self.assertTrue(config.diarization)

    def test_timestamp_plus_vocabulary_remains_a_capability_probe(self):
        invoke(self.client, custom_vocabulary=["文殊師利"], word_timestamp=True)
        config = self.client.models.generate_content.call_args.kwargs["config"].audio_transcription_config
        self.assertTrue(config.word_timestamp)
        self.assertEqual(config.custom_vocabulary, ["文殊師利"])

    def test_out_of_range_chunk_fails_closed(self):
        with self.assertRaises(ValueError):
            invoke(self.client, chunk=AudioChunk(0, 0, 901000))
        self.client.models.generate_content.assert_not_called()

    def test_independent_vocab_config(self):
        invoke(self.client, custom_vocabulary=["文殊師利"])
        cfg = self.client.models.generate_content.call_args.kwargs["config"].audio_transcription_config
        self.assertEqual(cfg.custom_vocabulary, ["文殊師利"])
        self.assertFalse(bool(cfg.word_timestamp))


if __name__ == "__main__":
    unittest.main()
