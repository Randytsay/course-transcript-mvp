from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor not found: {path}: {old[:90]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/providers/minimax_provider.py",
    '''            "raw_response": (\n                response\n                if isinstance(response, str)\n''',
    '''            "raw_response": (\n                _redact_text(response)\n                if isinstance(response, str)\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''        input_tokens = output_tokens = total_tokens = latency_ms = 0\n''',
    '''        input_tokens = output_tokens = reasoning_tokens = total_tokens = latency_ms = 0\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                output_tokens += int(completion.usage.get("output_tokens") or 0)\n                total_tokens += int(completion.usage.get("total_tokens") or 0)\n''',
    '''                output_tokens += int(completion.usage.get("output_tokens") or 0)\n                reasoning_tokens += int(completion.usage.get("reasoning_tokens") or 0)\n                total_tokens += int(completion.usage.get("total_tokens") or 0)\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                "output_tokens": output_tokens,\n                "total_tokens": total_tokens,\n''',
    '''                "output_tokens": output_tokens,\n                "reasoning_tokens": reasoning_tokens,\n                "total_tokens": total_tokens,\n''',
)

replace_once(
    "tests/test_minimax_provider.py",
    '''    def test_content_guard_falls_back_to_raw(self) -> None:\n''',
    '''    def test_terminology_aggregates_reasoning_tokens(self) -> None:\n        with tempfile.TemporaryDirectory() as directory:\n            key = Path(directory) / "key"\n            key.write_text("test-secret", encoding="utf-8")\n\n            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                payload = {\n                    "choices": [{\n                        "message": {\n                            "content": json.dumps({\n                                "terms": [{\n                                    "canonical": "MiniMax",\n                                    "variants": ["minimax"],\n                                    "confidence": "high",\n                                }]\n                            })\n                        }\n                    }],\n                    "usage": {\n                        "prompt_tokens": 20,\n                        "completion_tokens": 9,\n                        "completion_tokens_details": {"reasoning_tokens": 6},\n                    },\n                }\n                return 200, {}, json.dumps(payload).encode()\n\n            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)\n            result = client.extract_terms(ITEMS)\n            self.assertEqual(result["usage_metadata"]["reasoning_tokens"], 6)\n\n    def test_string_raw_provider_error_is_redacted_in_audit(self) -> None:\n        calls = 0\n        with tempfile.TemporaryDirectory() as directory:\n            key = Path(directory) / "key"\n            key.write_text("test-secret", encoding="utf-8")\n            audit = Path(directory) / "audit"\n\n            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                nonlocal calls\n                calls += 1\n                raise HTTPError(url, 500, "Server Error", {}, io.BytesIO(b"bearer sk-super-secret upstream unavailable"))\n\n            client = MiniMaxCorrectionClient(\n                key_file=key, http_post=http_post, sleeper=lambda _: None, audit_dir=audit\n            )\n            with self.assertRaises(MiniMaxProviderError):\n                client.correct_window(ITEMS, [])\n            self.assertEqual(calls, 3)\n            record = json.loads(next(audit.glob("*.json")).read_text(encoding="utf-8"))\n            self.assertNotIn("sk-super-secret", json.dumps(record))\n            self.assertIn("[REDACTED]", record["raw_response"])\n\n    def test_content_guard_falls_back_to_raw(self) -> None:\n''',
)
