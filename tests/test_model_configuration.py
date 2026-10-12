from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Text-only correction must use Vertex Gemini 3.7. Gemini 3.8 remains the
# separate audio-primary model, and therefore may appear in the env example.
TEXT_CORRECTION_FILES = (
    "app/api.py",
    "app/jobs/performance.py",
    "app/pipeline/dynamic_worker_hardened.py",
    "app/pipeline/dynamic_worker_observed.py",
    "app/pipeline/worker.py",
    "app/providers/correct_text.py",
    "app/providers/correct_text_cascade.py",
    "deploy/vertex-openai-proxy/litellm-config.yaml",
    "frontend/components/job-detail-page.tsx",
    "frontend/components/new-job-page.tsx",
)

AUDIO_PRIMARY_FILES = (
    "app/phase2_sample.py",
    "app/providers/gemini_primary_audio.py",
    "app/jobs/golden_v2_ledger.py",
    "app/pipeline/golden_v2_flow.py",
    "app/pipeline/golden_v2_primary_stage.py",
)

GENERIC_VERTEX_TEXT_FILES = (
    "app/infrastructure_test.py",
    "app/learning/generator.py",
)


class ModelConfigurationTests(unittest.TestCase):
    def test_text_correction_paths_use_gemini37_and_never_25(self) -> None:
        for relative in TEXT_CORRECTION_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertTrue(
                    "gemini-3.7-flash" in content or "Vertex Gemini 3.7" in content,
                    f"Vertex Gemini 3.7 correction target missing in {relative}",
                )
                self.assertNotIn("gemini-2.5", content)
                self.assertNotIn("gemini-3.8-flash", content)

    def test_audio_primary_paths_use_gemini38(self) -> None:
        for relative in AUDIO_PRIMARY_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("gemini-3.8-flash", content)

    def test_env_defaults_keep_audio_and_text_models_separate(self) -> None:
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("VERTEX_MODEL=gemini-3.8-flash", content)
        self.assertIn("CORRECTION_PRIMARY_MODEL=gemini-3.7-flash", content)
        self.assertIn("CORRECTION_ESCALATION_MODEL=gemini-3.7-flash", content)
        self.assertIn("PHASE2_MODEL=gemini-3.8-flash", content)

    def test_text_correction_proxy_aliases_all_target_gemini37(self) -> None:
        content = (ROOT / "deploy/vertex-openai-proxy/litellm-config.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("model: vertex_ai/gemini-3.8-flash", content)
        self.assertEqual(content.count("model: vertex_ai/gemini-3.7-flash"), 3)

    def test_gemini37_is_not_used_by_generic_text_generation(self) -> None:
        for relative in GENERIC_VERTEX_TEXT_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("gemini-3.7-flash", content)


if __name__ == "__main__":
    unittest.main()
