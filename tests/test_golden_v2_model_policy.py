"""Non-paid guardrails separating Gemini audio recognition and text correction.

This is a source-level regression test; it does not call Vertex and cannot
substitute for a real provider smoke test or prove production cutover.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = ROOT / "app" / "providers"


def assigned_string(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    return node.value.value
    raise AssertionError(f"expected constant {name} in {path}")


class GeminiModelPolicyTests(unittest.TestCase):
    def test_text_correction_targets_gemini38(self) -> None:
        self.assertEqual(
            assigned_string(PROVIDERS / "correct_text.py", "MODEL"),
            "gemini-3.8-flash",
        )

    def test_cascade_targets_gemini38(self) -> None:
        self.assertEqual(
            assigned_string(PROVIDERS / "correct_text_cascade.py", "ACTIVE_MODEL"),
            "gemini-3.8-flash",
        )

    def test_audio_primary_stays_gemini38(self) -> None:
        self.assertEqual(
            assigned_string(PROVIDERS / "gemini_primary_audio.py", "MODEL"),
            "gemini-3.8-flash",
        )

    def test_prompt_versions_record_text_model_and_exclude_25(self) -> None:
        for file in ("correct_text.py", "correct_text_cascade.py"):
            source = (PROVIDERS / file).read_text(encoding="utf-8")
            self.assertIn("gemini-3.8", source)
            self.assertNotIn("gemini-2.5", source)

    def test_legacy_generate_config_does_not_pass_disallowed_sampler_fields(self) -> None:
        for file in ("correct_text_legacy_hardened.py", "correct_text_cascade.py"):
            tree = ast.parse((PROVIDERS / file).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr != "GenerateContentConfig":
                        continue
                    keys = {keyword.arg for keyword in node.keywords}
                    self.assertFalse(
                        keys & {
                            "thinking_budget", "temperature", "top_p", "top_k",
                            "candidate_count", "frequency_penalty", "presence_penalty",
                        },
                        f"unsupported explicit sampling config in {file}",
                    )


if __name__ == "__main__":
    unittest.main()
