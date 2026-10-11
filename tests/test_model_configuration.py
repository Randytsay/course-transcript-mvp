from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Executable/configuration entrypoints for V2; older architecture and runbook
# documents deliberately continue to describe historical V1 evidence.
ACTIVE_MODEL_FILES = (
    "AGENTS.md",
    ".env.example",
    "app/api.py",
    "app/infrastructure_test.py",
    "app/jobs/performance.py",
    "app/phase2_sample.py",
    "app/pipeline/dynamic_worker_hardened.py",
    "app/pipeline/dynamic_worker_observed.py",
    "app/pipeline/worker.py",
    "app/providers/correct_text.py",
    "app/providers/correct_text_cascade.py",
    "deploy/vertex-openai-proxy/litellm-config.yaml",
    "frontend/components/job-detail-page.tsx",
    "frontend/components/new-job-page.tsx",
)


class ModelConfigurationTests(unittest.TestCase):
    def test_v2_active_paths_have_gemini_38_and_no_old_flash_targets(self) -> None:
        for relative in ACTIVE_MODEL_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("gemini-3.6-flash", content)
                self.assertNotIn("gemini-3.7-flash", content)
                self.assertTrue(
                    "gemini-3.8-flash" in content or "Gemini 3.8 Flash" in content
                    or "Gemini 3.8" in content,
                    f"Gemini 3.8 marker missing in {relative}",
                )

    def test_proxy_legacy_aliases_all_target_gemini_38(self) -> None:
        content = (ROOT / "deploy/vertex-openai-proxy/litellm-config.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("model: vertex_ai/gemini-3.7-flash", content)
        self.assertEqual(content.count("model: vertex_ai/gemini-3.8-flash"), 3)


if __name__ == "__main__":
    unittest.main()
