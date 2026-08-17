from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.providers.correction_routing import ProviderFailureKind
from app.providers.minimax_provider import MiniMaxProviderError
from app.providers.minimax_streaming_provider import MiniMaxStreamingCorrectionClient


ITEMS = [{"segment_id": "s1", "raw_text": "測試"}]


class MiniMaxStreamingFailClosedTests(unittest.TestCase):
    def test_http_401_fails_closed_without_retry(self) -> None:
        old = os.environ.get("MINIMAX_M3_STREAMING_ENABLED")
        os.environ["MINIMAX_M3_STREAMING_ENABLED"] = "true"
        try:
            with tempfile.TemporaryDirectory() as directory:
                key = Path(directory) / "key"
                key.write_text("test-secret", encoding="utf-8")
                calls = 0

                def stream_request(url, headers, body, deadline):
                    nonlocal calls
                    calls += 1
                    return {
                        "ok": False,
                        "status_code": 401,
                        "error_type": "http_error",
                        "error_payload": "unauthorized",
                    }

                client = MiniMaxStreamingCorrectionClient(
                    key_file=key,
                    stream_request=stream_request,
                    sleeper=lambda _: None,
                )
                with self.assertRaises(MiniMaxProviderError) as context:
                    client.correct_window(ITEMS, [])
                self.assertEqual(calls, 1)
                self.assertEqual(context.exception.kind, ProviderFailureKind.AUTHENTICATION)
        finally:
            if old is None:
                os.environ.pop("MINIMAX_M3_STREAMING_ENABLED", None)
            else:
                os.environ["MINIMAX_M3_STREAMING_ENABLED"] = old


if __name__ == "__main__":
    unittest.main()
