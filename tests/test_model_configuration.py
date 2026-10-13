from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Text-only correction must use Vertex Gemini 3.8. Gemini 3.8 remains the
# separate audio-primary model, and therefore may appear in the env example.
TEXT_CORRECTION_FILES = (
    "app/api.py",
    "app/api_ext.py",
    "app/jobs/performance.py",
    "app/pipeline/dynamic_worker_hardened.py",
    "app/pipeline/dynamic_worker_observed.py",
    "app/pipeline/worker.py",
    "app/providers/correct_text.py",
    "app/providers/correct_text_cascade.py",
    "app/providers/fake_correction.py",
    "app/providers/correction/openrouter.py",
    "deploy/vertex-openai-proxy/litellm-config.yaml",
    "frontend/components/job-detail-page.tsx",
    "frontend/components/new-job-page.tsx",
    "frontend/components/new-job-page-drive-api.tsx",
    "frontend/app/review-admin/ai-providers/page.tsx",
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
    def test_text_correction_paths_use_gemini38_and_never_25(self) -> None:
        for relative in TEXT_CORRECTION_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertTrue(
                    "gemini-3.8-flash" in content or "Vertex Gemini 3.8" in content,
                    f"Vertex Gemini 3.8 correction target missing in {relative}",
                )
                self.assertNotIn("gemini-2.5", content)
                self.assertNotIn("gemini-3.7-flash", content)

    def test_3_8_text_adapter_configuration_is_free_of_legacy_sampler_fields(self) -> None:
        import ast
        for relative in ("app/providers/correct_text.py", "app/providers/correction/vertex.py"):
            with self.subTest(path=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                for call in ast.walk(tree):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                        if call.func.attr == "GenerateContentConfig":
                            self.assertFalse(
                                {"temperature", "top_k", "top_p", "thinking_budget",
                                 "candidate_count", "frequency_penalty", "presence_penalty"}
                                & {kw.arg for kw in call.keywords},
                                f"deprecated Gemini 3.8 field in {relative}",
                            )

    def test_audio_primary_paths_use_gemini38(self) -> None:
        for relative in AUDIO_PRIMARY_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("gemini-3.8-flash", content)

    def test_env_defaults_keep_audio_and_text_models_separate(self) -> None:
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("VERTEX_MODEL=gemini-3.8-flash", content)
        self.assertIn("CORRECTION_PRIMARY_MODEL=gemini-3.8-flash", content)
        self.assertIn("CORRECTION_ESCALATION_MODEL=gemini-3.8-flash", content)
        self.assertIn("PHASE2_MODEL=gemini-3.8-flash", content)

    def test_text_correction_proxy_aliases_all_target_gemini38(self) -> None:
        content = (ROOT / "deploy/vertex-openai-proxy/litellm-config.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("model: vertex_ai/gemini-3.7-flash", content)
        self.assertEqual(content.count("model: vertex_ai/gemini-3.8-flash"), 3)

    def test_generic_text_generation_does_not_reference_legacy_37(self) -> None:
        for relative in GENERIC_VERTEX_TEXT_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("gemini-3.7-flash", content)


if __name__ == "__main__":
    unittest.main()
