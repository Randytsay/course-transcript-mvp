from __future__ import annotations

import time
import unittest

from app.providers.minimax_streaming import parse_sse_lines, run_strict_stream


def _slow_worker(connection, url, headers, body, socket_timeout):
    time.sleep(2.0)
    connection.send({"ok": True, "content": "partial-must-not-escape"})
    connection.close()


class MiniMaxStreamingTests(unittest.TestCase):
    def test_parser_reconstructs_final_content_and_usage(self) -> None:
        parsed = parse_sse_lines(
            [
                'data: {"choices":[{"delta":{"content":"{\\"segments\\":["},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{"content":"]}"},"finish_reason":"stop"}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}',
                "data: [DONE]",
            ]
        )
        self.assertEqual(parsed["content"], '{"segments":[]}')
        self.assertEqual(parsed["finish_reason"], "stop")
        self.assertEqual(parsed["usage"]["prompt_tokens"], 10)
        self.assertTrue(parsed["done_seen"])

    def test_parser_rejects_malformed_sse(self) -> None:
        with self.assertRaisesRegex(ValueError, "malformed_sse_event"):
            parse_sse_lines(["not-sse"])
        with self.assertRaisesRegex(ValueError, "malformed_sse_json"):
            parse_sse_lines(["data: {broken"])

    def test_hard_deadline_discards_child_partial_result(self) -> None:
        try:
            result = run_strict_stream(
                "https://example.invalid",
                {},
                b"{}",
                1.0,
                worker=_slow_worker,
                start_method="fork",
            )
        except ValueError:
            self.skipTest("fork start method unavailable")
        self.assertFalse(result["ok"])
        self.assertTrue(result["deadline_exceeded"])
        self.assertEqual(result["error_type"], "wall_clock_deadline")
        self.assertNotIn("content", result)
        self.assertNotIn("usage", result)


if __name__ == "__main__":
    unittest.main()
