"""Non-paid Gemini 3.8 Flash migration smoke tests.

These tests mock every SDK call. They cannot confirm Vertex project model access
or represent permission to submit a real billable request.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.providers import correct_text as base
from app.providers import correct_text_cascade as cascade
from app.providers import correct_text_legacy_hardened as legacy
from app.providers.correction.vertex import VertexCorrectionProvider


class Gemini38MigrationTests(unittest.TestCase):
    def test_3_8_is_current_new_provider_default(self) -> None:
        self.assertEqual(base.MODEL, "gemini-3.8-flash")
        self.assertEqual(cascade.ACTIVE_MODEL, "gemini-3.8-flash")
        self.assertEqual(VertexCorrectionProvider.default_model, "gemini-3.8-flash")

    def test_text_correction_excludes_legacy_sampling_parameters(self) -> None:
        response = SimpleNamespace(text='{"segments":[]}', usage_metadata=None)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=mock.Mock(return_value=response)))
        with mock.patch.object(base, "client", return_value=client):
            got, _metrics = base.generate_json("fake instruction", base.CORRECTION_SCHEMA)
        self.assertIs(got, response)
        params = client.models.generate_content.call_args.kwargs
        self.assertEqual(params["model"], "gemini-3.8-flash")
        config = params["config"]
        self.assertIsNone(config.temperature)
        self.assertIsNone(config.top_p)
        self.assertIsNone(config.top_k)
        self.assertIsNone(config.candidate_count)
        self.assertIsNotNone(config.thinking_config)
        self.assertEqual(str(config.thinking_config.thinking_level).upper(), "LOW")

    def test_hardened_and_cascade_use_38_thinking_config(self) -> None:
        response = SimpleNamespace(text='{"segments":[]}')
        client = SimpleNamespace(models=SimpleNamespace(generate_content=mock.Mock(return_value=response)))
        with mock.patch.object(base, "client", return_value=client):
            legacy.generate_json("fake", base.CORRECTION_SCHEMA)
            cascade._generate("gemini-3.8-flash", "fake")
        self.assertEqual(client.models.generate_content.call_count, 2)
        for call in client.models.generate_content.call_args_list:
            config = call.kwargs["config"]
            self.assertIsNone(config.temperature)
            self.assertIsNotNone(config.thinking_config)

    def test_sample_environment_defaults_to_38(self) -> None:
        text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
        self.assertIn("VERTEX_MODEL=gemini-3.8-flash", text)
        self.assertIn("CORRECTION_PRIMARY_MODEL=gemini-3.8-flash", text)
        self.assertIn("CORRECTION_ESCALATION_MODEL=gemini-3.8-flash", text)
        self.assertNotIn("VERTEX_MODEL=gemini-3.7-flash", text)


if __name__ == "__main__":
    unittest.main()
