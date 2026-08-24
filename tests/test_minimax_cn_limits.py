from __future__ import annotations

import unittest

from app.providers.correction.base import ProviderError
from app.providers.correction.minimax import MiniMaxCorrectionProvider


def _capture_payload(base_url: str, max_completion_tokens: int) -> dict:
    captured: dict = {}

    def http(method, url, headers, payload=None):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return 200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "[]"},
            }],
            "usage": {},
        }

    provider = MiniMaxCorrectionProvider(
        api_key="fake-minimax-key",
        base_url=base_url,
        max_completion_tokens=max_completion_tokens,
        http=http,
    )
    provider.realtime_generate("test")
    return captured


def _error_for(body: dict, *, status: int = 422) -> ProviderError:
    def http(method, url, headers, payload=None):
        return status, body

    provider = MiniMaxCorrectionProvider(
        api_key="fake-minimax-key",
        base_url="https://api.minimaxi.com",
        http=http,
    )
    try:
        provider.realtime_generate("TOP_SECRET_TRANSCRIPT")
    except ProviderError as exc:
        return exc
    raise AssertionError("expected ProviderError")


class MiniMaxRequestContractTests(unittest.TestCase):
    def test_cn_endpoint_preserves_valid_4096_completion_tokens(self):
        captured = _capture_payload("https://api.minimaxi.com", 4096)
        self.assertEqual(captured["url"], "https://api.minimaxi.com/v1/chat/completions")
        self.assertEqual(captured["payload"]["max_completion_tokens"], 4096)

    def test_cn_endpoint_preserves_lower_max_completion_tokens(self):
        captured = _capture_payload("https://api.minimaxi.com/v1", 1024)
        self.assertEqual(captured["payload"]["max_completion_tokens"], 1024)

    def test_global_endpoint_keeps_existing_4096_default_behavior(self):
        captured = _capture_payload("https://api.minimax.io", 4096)
        self.assertEqual(captured["url"], "https://api.minimax.io/v1/chat/completions")
        self.assertEqual(captured["payload"]["max_completion_tokens"], 4096)

    def test_m3_openai_payload_keeps_supported_request_contract(self):
        captured = _capture_payload("https://api.minimaxi.com", 4096)
        payload = captured["payload"]
        self.assertEqual(payload["model"], "MiniMax-M3")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertIs(payload["reasoning_split"], True)
        self.assertEqual(payload["max_completion_tokens"], 4096)


class MiniMaxSafe422DiagnosticTests(unittest.TestCase):
    def test_422_detail_exposes_only_bounded_validation_structure(self):
        exc = _error_for({
            "detail": [{
                "type": "value_error",
                "loc": ["body", "thinking", "type"],
                "msg": "TOP_SECRET_PROVIDER_MESSAGE",
                "input": "TOP_SECRET_INPUT",
            }],
        })
        message = exc.safe_message
        self.assertEqual(exc.kind, "invalid_request")
        self.assertIn("HTTP 422", message)
        self.assertIn("validation=loc:body.thinking.type,type:value_error", message)
        self.assertNotIn("TOP_SECRET_PROVIDER_MESSAGE", message)
        self.assertNotIn("TOP_SECRET_INPUT", message)
        self.assertNotIn("TOP_SECRET_TRANSCRIPT", message)

    def test_422_error_exposes_safe_type_param_code_not_message(self):
        exc = _error_for({
            "error": {
                "code": "bad_parameter",
                "type": "invalid_request_error",
                "param": "reasoning_split",
                "message": "TOP_SECRET_PROVIDER_MESSAGE",
            },
        })
        message = exc.safe_message
        self.assertEqual(exc.kind, "invalid_request")
        self.assertIn("provider code=bad_parameter", message)
        self.assertIn("error_type=invalid_request_error", message)
        self.assertIn("param=reasoning_split", message)
        self.assertNotIn("TOP_SECRET_PROVIDER_MESSAGE", message)

    def test_422_content_rejection_is_categorized_without_echoing_message(self):
        exc = _error_for({
            "message": "Content moderation rejected TOP_SECRET_PROVIDER_MESSAGE",
        })
        message = exc.safe_message
        self.assertEqual(exc.kind, "invalid_request")
        self.assertIn("category=content_rejected", message)
        self.assertNotIn("TOP_SECRET_PROVIDER_MESSAGE", message)

    def test_422_rejects_arbitrary_metadata_tokens_in_safe_error(self):
        exc = _error_for({
            "error": {
                "type": "invalid request with spaces TOP_SECRET_TYPE",
                "param": "../../TOP_SECRET_PARAM",
                "message": "TOP_SECRET_PROVIDER_MESSAGE",
            },
            "detail": [{
                "type": "value error TOP_SECRET_DETAIL",
                "loc": ["body", "TOP SECRET LOC"],
                "msg": "TOP_SECRET_DETAIL_MESSAGE",
            }],
        })
        message = exc.safe_message
        for secret in (
            "TOP_SECRET_TYPE",
            "TOP_SECRET_PARAM",
            "TOP_SECRET_PROVIDER_MESSAGE",
            "TOP_SECRET_DETAIL",
            "TOP_SECRET_DETAIL_MESSAGE",
            "TOP SECRET LOC",
        ):
            self.assertNotIn(secret, message)


if __name__ == "__main__":
    unittest.main()
