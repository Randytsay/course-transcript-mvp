from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor not found: {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/providers/minimax_provider.py",
    '''        raw_thinking_mode = os.getenv("MINIMAX_M3_THINKING_MODE", "disabled").strip().lower()\n        self.thinking_mode = raw_thinking_mode if raw_thinking_mode in {"disabled", "adaptive"} else "disabled"\n''',
    '''        raw_correction_thinking = os.getenv("MINIMAX_M3_CORRECTION_THINKING_MODE", "disabled").strip().lower()\n        self.correction_thinking_mode = (\n            raw_correction_thinking if raw_correction_thinking in {"disabled", "adaptive"} else "disabled"\n        )\n        raw_terminology_thinking = os.getenv("MINIMAX_M3_TERMINOLOGY_THINKING_MODE", "adaptive").strip().lower()\n        self.terminology_thinking_mode = (\n            raw_terminology_thinking if raw_terminology_thinking in {"disabled", "adaptive"} else "adaptive"\n        )\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''            "operation": operation,\n            "reasoning_split": self.reasoning_split,\n''',
    '''            "operation": operation,\n            "reasoning_split": self.reasoning_split,\n            "thinking_mode": (\n                self.terminology_thinking_mode if operation == "terminology" else self.correction_thinking_mode\n            ),\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''    def _request(self, prompt: str, items: list[dict[str, Any]], *, system_prompt: str | None = None) -> MiniMaxCompletion:\n        key = self._key()\n''',
    '''    def _request(\n        self,\n        prompt: str,\n        items: list[dict[str, Any]],\n        *,\n        system_prompt: str | None = None,\n        thinking_mode: str | None = None,\n    ) -> MiniMaxCompletion:\n        key = self._key()\n        selected_thinking_mode = thinking_mode or self.correction_thinking_mode\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                "thinking": {"type": self.thinking_mode},\n''',
    '''                "thinking": {"type": selected_thinking_mode},\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                completion = self._request(prompt, items, system_prompt=system)\n''',
    '''                completion = self._request(\n                    prompt,\n                    items,\n                    system_prompt=system,\n                    thinking_mode=self.terminology_thinking_mode,\n                )\n''',
)

replace_once(
    "app/providers/correction_runtime.py",
    '''        self.m3_thinking_mode = _safe_runtime_ref(getattr(self.m3_client, "thinking_mode", None))\n''',
    '''        self.m3_thinking_mode = _safe_runtime_ref(\n            getattr(self.m3_client, "correction_thinking_mode", None)\n        )\n''',
)

replace_once(
    ".env.example",
    '''# MiniMax-M3 officially supports disabled/adaptive thinking. Fixed-segment\n# correction and terminology are deterministic transformations, so keep\n# thinking disabled unless a bounded quality experiment proves it is needed.\nMINIMAX_M3_THINKING_MODE=disabled\n''',
    '''# MiniMax-M3 officially supports disabled/adaptive thinking. Fixed-segment\n# correction is deterministic, so disable thinking by default to avoid spending\n# output budget and latency on unnecessary reasoning. Terminology extraction\n# was already stable in Phase B/C, so preserve adaptive thinking there until a\n# separate glossary-quality experiment justifies changing it.\nMINIMAX_M3_CORRECTION_THINKING_MODE=disabled\nMINIMAX_M3_TERMINOLOGY_THINKING_MODE=adaptive\n''',
)

replace_once(
    "tests/test_minimax_provider.py",
    '''            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                payload = {\n                    "choices": [{\n                        "message": {\n                            "content": json.dumps({\n                                "terms": [{\n''',
    '''            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                request = json.loads(body.decode("utf-8"))\n                self.assertEqual(request["thinking"], {"type": "adaptive"})\n                payload = {\n                    "choices": [{\n                        "message": {\n                            "content": json.dumps({\n                                "terms": [{\n''',
)

replace_once(
    "tests/test_correction_runtime.py",
    '''        self.thinking_mode = "disabled"\n''',
    '''        self.correction_thinking_mode = "disabled"\n''',
)
