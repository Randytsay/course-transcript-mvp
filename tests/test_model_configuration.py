from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_MODEL_FILES = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "RUNBOOK.md",
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
    "frontend/components/dashboard-page.tsx",
    "frontend/components/job-detail-page.tsx",
    "frontend/components/new-job-page.tsx",
)


class ModelConfigurationTests(unittest.TestCase):
    def test_active_paths_do_not_drift_back_to_gemini_36(self) -> None:
        for relative in ACTIVE_MODEL_FILES:
            content = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(
                "gemini-3.6-flash",
                content,
                f"active model drifted in {relative}",
            )
            self.assertTrue(
                "gemini-3.7-flash" in content or "Gemini 3.7 Flash" in content,
                f"Gemini 3.7 marker missing in {relative}",
            )

    def test_proxy_keeps_legacy_aliases_but_all_resolve_to_gemini_37(self) -> None:
        content = (ROOT / "deploy/vertex-openai-proxy/litellm-config.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("model: vertex_ai/gemini-3.6-flash", content)
        self.assertEqual(content.count("model: vertex_ai/gemini-3.7-flash"), 3)


if __name__ == "__main__":
    unittest.main()
