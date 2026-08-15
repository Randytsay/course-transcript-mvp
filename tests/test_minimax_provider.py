from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.correction_routing import ProviderFailureKind
from app.providers.minimax_provider import MiniMaxCorrectionClient, MiniMaxProviderError


ITEMS = [
    {"segment_id": "s1", "start_ms": 0, "end_ms": 1000, "raw_text": "這是一段課程內容"},
]


class MiniMaxProviderTests(unittest.TestCase):
    def test_invalid_structured_response_is_retried_before_accepting_terms(self) -> None:
        calls = 0
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                nonlocal calls
                calls += 1
                content = (
                    '{"terms":[{"canonical":"錯誤","variants":"not-an-array","confidence":"high"}]}'
                    if calls == 1
                    else '{"terms":[{"canonical":"正確術語","variants":["正確術語"],"confidence":"high"}]}'
                )
                return 200, {}, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            terms = client.extract_terms(ITEMS)
            self.assertEqual(calls, 2)
            self.assertEqual(terms[0]["canonical"], "正確術語")

    def test_structured_response_preserves_ids_and_timestamps_are_not_in_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("sk-test-secret", encoding="utf-8")
            audit = Path(directory) / "audit"

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                payload = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "segments": [
                                            {
                                                "segment_id": "s1",
                                                "corrected_text": "這是一段課程內容。",
                                                "uncertain_terms": [],
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
                return 200, {}, json.dumps(payload, ensure_ascii=False).encode()

            client = MiniMaxCorrectionClient(
                key_file=key,
                http_post=http_post,
                sleeper=lambda _: None,
                audit_dir=audit,
            )
            result = client.correct_window(ITEMS, [], context="general")
            self.assertEqual(result["s1"]["corrected_text"], "這是一段課程內容。")
            self.assertNotIn("start_ms", result["s1"])
            record = json.loads(next(audit.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(record["usage_metadata"]["input_tokens"], 11)
            self.assertNotIn("sk-test-secret", json.dumps(record, ensure_ascii=False))

    def test_reasoning_wrapper_is_removed_before_structured_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                content = (
                    "<think>internal provider reasoning</think>\n"
                    + json.dumps(
                        {
                            "segments": [
                                {
                                    "segment_id": "s1",
                                    "corrected_text": "這是一段課程內容。",
                                    "uncertain_terms": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
                return 200, {}, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            result = client.correct_window(ITEMS, [])
            self.assertEqual(result["s1"]["corrected_text"], "這是一段課程內容。")

    def test_reasoning_wrapper_and_inner_fenced_json_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                content = (
                    "<think>provider reasoning</think>\n"
                    "```json\n"
                    + json.dumps(
                        {
                            "segments": [
                                {
                                    "segment_id": "s1",
                                    "corrected_text": "這是一段課程內容。",
                                    "uncertain_terms": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                    + "\n```"
                )
                return 200, {}, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            result = client.correct_window(ITEMS, [])
            self.assertEqual(result["s1"]["corrected_text"], "這是一段課程內容。")

    def test_content_guard_falls_back_to_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                content = {"segments": [{"segment_id": "s1", "corrected_text": "完全不同的長篇改寫內容，沒有保留原始課程語意", "uncertain_terms": []}]}
                return 200, {}, json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode()

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            result = client.correct_window(ITEMS, [])
            self.assertEqual(result["s1"]["corrected_text"], ITEMS[0]["raw_text"])
            self.assertTrue(result["s1"]["fallback_to_raw"])

    def test_rate_limit_is_bounded_and_exhausted_as_transient(self) -> None:
        calls = 0
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                nonlocal calls
                calls += 1
                return 429, {}, b'{"base_resp":{"status_code":1004,"status_msg":"rate limit"}}'

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 3)
            self.assertEqual(context.exception.kind, ProviderFailureKind.TRANSIENT_EXHAUSTED)

    def test_authentication_error_is_not_converted_to_quota_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                return 401, {}, b'{"base_resp":{"status_code":1001,"status_msg":"unauthorized"}}'

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(context.exception.kind, ProviderFailureKind.AUTHENTICATION)


if __name__ == "__main__":
    unittest.main()
