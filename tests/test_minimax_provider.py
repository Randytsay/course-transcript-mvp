from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from app.providers.correction_routing import ProviderFailureKind
from app.providers.minimax_provider import MiniMaxCorrectionClient, MiniMaxProviderError


ITEMS = [
    {"segment_id": "s1", "start_ms": 0, "end_ms": 1000, "raw_text": "這是一段課程內容"},
]


class MiniMaxProviderTests(unittest.TestCase):
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
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "completion_tokens_details": {"reasoning_tokens": 13},
                    },
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
            self.assertEqual(record["usage_metadata"]["reasoning_tokens"], 13)
            self.assertTrue(record["reasoning_split"])
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

    def test_live_m3_reasoning_split_is_requested_and_final_content_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                request = json.loads(body.decode("utf-8"))
                self.assertTrue(request["reasoning_split"])
                payload = {
                    "model": "MiniMax-M3",
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({
                                "segments": [{
                                    "segment_id": "s1",
                                    "corrected_text": "這是一段課程內容。",
                                    "uncertain_terms": [],
                                }]
                            }, ensure_ascii=False),
                            "reasoning": "provider reasoning",
                            "reasoning_content": "provider reasoning",
                        },
                    }],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
                return 200, {}, json.dumps(payload, ensure_ascii=False).encode()

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            result = client.correct_window(ITEMS, [])
            self.assertEqual(result["s1"]["corrected_text"], "這是一段課程內容。")

    def test_terminology_aggregates_reasoning_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                payload = {
                    "choices": [{
                        "message": {
                            "content": json.dumps({
                                "terms": [{
                                    "canonical": "MiniMax",
                                    "variants": ["minimax"],
                                    "confidence": "high",
                                }]
                            })
                        }
                    }],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 9,
                        "completion_tokens_details": {"reasoning_tokens": 6},
                    },
                }
                return 200, {}, json.dumps(payload).encode()

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            result = client.extract_terms(ITEMS)
            self.assertEqual(result["usage_metadata"]["reasoning_tokens"], 6)

    def test_string_raw_provider_error_is_redacted_in_audit(self) -> None:
        calls = 0
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")
            audit = Path(directory) / "audit"

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                nonlocal calls
                calls += 1
                raise HTTPError(url, 500, "Server Error", {}, io.BytesIO(b"bearer sk-super-secret upstream unavailable"))

            client = MiniMaxCorrectionClient(
                key_file=key, http_post=http_post, sleeper=lambda _: None, audit_dir=audit
            )
            with self.assertRaises(MiniMaxProviderError):
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 3)
            record = json.loads(next(audit.glob("*.json")).read_text(encoding="utf-8"))
            self.assertNotIn("sk-super-secret", json.dumps(record))
            self.assertIn("[REDACTED]", record["raw_response"])

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

    def test_real_urllib_http_error_authentication_fails_closed_without_retry(self) -> None:
        calls = 0
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                nonlocal calls
                calls += 1
                raise HTTPError(
                    url,
                    401,
                    "Unauthorized",
                    {},
                    io.BytesIO(b'{"base_resp":{"status_code":1001,"status_msg":"unauthorized"}}'),
                )

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 1)
            self.assertEqual(context.exception.kind, ProviderFailureKind.AUTHENTICATION)

    def test_real_urllib_http_error_usage_limit_fails_closed_without_retry(self) -> None:
        calls = 0
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-secret", encoding="utf-8")

            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
                nonlocal calls
                calls += 1
                raise HTTPError(
                    url,
                    402,
                    "Payment Required",
                    {},
                    io.BytesIO(b'{"base_resp":{"status_code":1008,"status_msg":"quota exhausted"}}'),
                )

            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 1)
            self.assertEqual(context.exception.kind, ProviderFailureKind.USAGE_LIMIT)

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
