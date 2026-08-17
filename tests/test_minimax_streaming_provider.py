from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.providers.correction_routing import ProviderFailureKind
from app.providers.minimax_provider import MiniMaxProviderError
from app.providers.minimax_streaming_provider import MiniMaxStreamingCorrectionClient


ITEMS = [
    {"segment_id": "s1", "start_ms": 0, "end_ms": 1000, "raw_text": "這是一段課程內容"},
]


class MiniMaxStreamingProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_stream = os.environ.get("MINIMAX_M3_STREAMING_ENABLED")
        self.old_deadline = os.environ.get("MINIMAX_M3_STREAM_DEADLINE_SECONDS")
        os.environ["MINIMAX_M3_STREAMING_ENABLED"] = "true"
        os.environ["MINIMAX_M3_STREAM_DEADLINE_SECONDS"] = "75"

    def tearDown(self) -> None:
        if self.old_stream is None:
            os.environ.pop("MINIMAX_M3_STREAMING_ENABLED", None)
        else:
            os.environ["MINIMAX_M3_STREAMING_ENABLED"] = self.old_stream
        if self.old_deadline is None:
            os.environ.pop("MINIMAX_M3_STREAM_DEADLINE_SECONDS", None)
        else:
            os.environ["MINIMAX_M3_STREAM_DEADLINE_SECONDS"] = self.old_deadline

    def _key(self, directory: str) -> Path:
        path = Path(directory) / "key"
        path.write_text("test-secret", encoding="utf-8")
        return path

    def test_success_uses_strict_streaming_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                request = json.loads(body.decode("utf-8"))
                self.assertTrue(request["stream"])
                self.assertEqual(request["stream_options"], {"include_usage": True})
                self.assertEqual(request["thinking"], {"type": "disabled"})
                self.assertEqual(request["max_completion_tokens"], 4096)
                self.assertEqual(deadline, 75.0)
                return {
                    "ok": True,
                    "status_code": 200,
                    "finish_reason": "stop",
                    "content": json.dumps(
                        {"segments": [{"segment_id": "s1", "corrected_text": "這是一段課程內容。", "uncertain_terms": []}]},
                        ensure_ascii=False,
                    ),
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                    "latency_ms": 3200,
                    "first_event_ms": 700,
                    "event_count": 4,
                    "done_seen": True,
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            result = client.correct_window(ITEMS, [])
            self.assertEqual(calls, 1)
            self.assertEqual(result["s1"]["corrected_text"], "這是一段課程內容。")
            self.assertNotIn("start_ms", result["s1"])
            self.assertEqual(client._last_attempts[0]["transport"], "streaming_v2")
            self.assertEqual(client._last_attempts[0]["stream_first_event_ms"], 700)

    def test_finish_reason_length_is_discarded_without_transport_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": True,
                    "status_code": 200,
                    "finish_reason": "length",
                    "content": "{partial",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 1)
            self.assertEqual(context.exception.kind, ProviderFailureKind.OUTPUT_LIMIT)

    def test_missing_usage_is_invalid_and_never_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": True,
                    "status_code": 200,
                    "finish_reason": "stop",
                    "content": json.dumps({"segments": [{"segment_id": "s1", "corrected_text": "OK", "uncertain_terms": []}]}),
                    "usage": None,
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 2)
            self.assertEqual(context.exception.kind, ProviderFailureKind.INVALID_RESPONSE)

    def test_streamed_timestamp_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": True,
                    "status_code": 200,
                    "finish_reason": "stop",
                    "content": json.dumps({
                        "segments": [{
                            "segment_id": "s1",
                            "corrected_text": "這是一段課程內容。",
                            "uncertain_terms": [],
                            "start_ms": 999,
                        }]
                    }, ensure_ascii=False),
                    "usage": {"prompt_tokens": 11, "completion_tokens": 9},
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 2)
            self.assertEqual(context.exception.kind, ProviderFailureKind.INVALID_RESPONSE)
            self.assertEqual(context.exception.raw_response["shape_error"], "forbidden_fields")

    def test_streamed_segment_order_must_match_source_exactly(self) -> None:
        items = [
            {"segment_id": "s1", "raw_text": "第一段"},
            {"segment_id": "s2", "raw_text": "第二段"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": True,
                    "status_code": 200,
                    "finish_reason": "stop",
                    "content": json.dumps({
                        "segments": [
                            {"segment_id": "s2", "corrected_text": "第二段。", "uncertain_terms": []},
                            {"segment_id": "s1", "corrected_text": "第一段。", "uncertain_terms": []},
                        ]
                    }, ensure_ascii=False),
                    "usage": {"prompt_tokens": 12, "completion_tokens": 12},
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(items, [])
            self.assertEqual(calls, 2)
            self.assertEqual(context.exception.kind, ProviderFailureKind.INVALID_RESPONSE)
            self.assertEqual(context.exception.raw_response["shape_error"], "segment_id_order")

    def test_deadline_failure_is_bounded_transient_and_contains_no_partial_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": False,
                    "status_code": None,
                    "deadline_exceeded": True,
                    "latency_ms": 75000,
                    "error_type": "wall_clock_deadline",
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client.correct_window(ITEMS, [])
            self.assertEqual(calls, 3)
            self.assertEqual(context.exception.kind, ProviderFailureKind.TRANSIENT_EXHAUSTED)
            self.assertNotIn("partial", json.dumps(context.exception.raw_response or {}))

    def test_terminology_stays_on_existing_non_stream_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stream_calls = 0
            http_calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal stream_calls
                stream_calls += 1
                raise AssertionError("terminology must not stream")

            def http_post(url, headers, body, timeout):
                nonlocal http_calls
                http_calls += 1
                request = json.loads(body.decode("utf-8"))
                self.assertFalse(request["stream"])
                self.assertEqual(request["thinking"], {"type": "adaptive"})
                payload = {
                    "choices": [{"message": {"content": json.dumps({"terms": [{"canonical": "MiniMax", "variants": ["minimax"], "confidence": "high"}]})}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8},
                }
                return 200, {}, json.dumps(payload).encode()

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                http_post=http_post,
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            result = client.extract_terms(ITEMS)
            self.assertEqual(stream_calls, 0)
            self.assertEqual(http_calls, 1)
            self.assertEqual(result["terms"][0]["canonical"], "MiniMax")


if __name__ == "__main__":
    unittest.main()
