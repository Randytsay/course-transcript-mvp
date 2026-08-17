from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.providers.correction_routing import ProviderFailureKind
from app.providers.minimax_provider import MiniMaxProviderError
from app.providers.minimax_streaming import (
    error_fingerprint,
    extract_provider_error_code,
    safe_trace_id,
)
from app.providers.minimax_streaming_provider import MiniMaxStreamingCorrectionClient


ITEMS = [
    {"segment_id": "s1", "start_ms": 0, "end_ms": 1000, "raw_text": "這是一段課程內容"},
]


class MiniMaxStreamingProviderErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_stream = os.environ.get("MINIMAX_M3_STREAMING_ENABLED")
        self.old_attempts = os.environ.get("MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS")
        os.environ["MINIMAX_M3_STREAMING_ENABLED"] = "true"
        os.environ["MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS"] = "3"

    def tearDown(self) -> None:
        if self.old_stream is None:
            os.environ.pop("MINIMAX_M3_STREAMING_ENABLED", None)
        else:
            os.environ["MINIMAX_M3_STREAMING_ENABLED"] = self.old_stream
        if self.old_attempts is None:
            os.environ.pop("MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS", None)
        else:
            os.environ["MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS"] = self.old_attempts

    def _key(self, directory: str) -> Path:
        path = Path(directory) / "key"
        path.write_text("test-secret", encoding="utf-8")
        return path

    def test_safe_provider_error_metadata_helpers(self) -> None:
        raw = b'{"base_resp":{"status_code":1001,"status_msg":"request timeout"}}'
        self.assertEqual(extract_provider_error_code(raw), 1001)
        self.assertEqual(extract_provider_error_code(raw.decode()), 1001)
        self.assertEqual(
            safe_trace_id({"Trace_Id": "abc-123_DEF:456"}),
            "abc-123_DEF:456",
        )
        self.assertIsNone(safe_trace_id({"trace_id": "not safe with spaces"}))
        self.assertEqual(error_fingerprint(raw), error_fingerprint(raw))
        self.assertEqual(len(error_fingerprint(raw) or ""), 24)

    def test_http_422_provider_timeout_is_bounded_transient_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": False,
                    "status_code": 422,
                    "error_type": "http_error",
                    "provider_error_code": 1001,
                    "provider_trace_id": "trace-timeout-1",
                    "provider_error_fingerprint": "abc123",
                    "provider_error_bytes": 42,
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client._request("prompt", ITEMS)
            self.assertEqual(calls, 3)
            self.assertEqual(context.exception.kind, ProviderFailureKind.TRANSIENT_EXHAUSTED)
            self.assertEqual(client._last_attempts[-1]["provider_error_code"], 1001)
            self.assertEqual(client._last_attempts[-1]["provider_trace_id"], "trace-timeout-1")
            self.assertNotIn("error_payload", context.exception.raw_response or {})

    def test_http_422_parameter_error_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": False,
                    "status_code": 422,
                    "error_type": "http_error",
                    "provider_error_code": 2013,
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client._request("prompt", ITEMS)
            self.assertEqual(calls, 1)
            self.assertEqual(context.exception.kind, ProviderFailureKind.INVALID_RESPONSE)

    def test_http_422_token_plan_limit_fails_fast_as_usage_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": False,
                    "status_code": 422,
                    "error_type": "http_error",
                    "provider_error_code": 2056,
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client._request("prompt", ITEMS)
            self.assertEqual(calls, 1)
            self.assertEqual(context.exception.kind, ProviderFailureKind.USAGE_LIMIT)

    def test_http_422_token_limit_is_output_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": False,
                    "status_code": 422,
                    "error_type": "http_error",
                    "provider_error_code": 1039,
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client._request("prompt", ITEMS)
            self.assertEqual(calls, 1)
            self.assertEqual(context.exception.kind, ProviderFailureKind.OUTPUT_LIMIT)

    def test_http_status_auth_precedes_conflicting_provider_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def stream_request(url, headers, body, deadline):
                nonlocal calls
                calls += 1
                return {
                    "ok": False,
                    "status_code": 401,
                    "error_type": "http_error",
                    "provider_error_code": 1001,
                }

            client = MiniMaxStreamingCorrectionClient(
                key_file=self._key(directory),
                stream_request=stream_request,
                sleeper=lambda _: None,
            )
            with self.assertRaises(MiniMaxProviderError) as context:
                client._request("prompt", ITEMS)
            self.assertEqual(calls, 1)
            self.assertEqual(context.exception.kind, ProviderFailureKind.AUTHENTICATION)


if __name__ == "__main__":
    unittest.main()
