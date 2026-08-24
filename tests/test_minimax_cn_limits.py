from __future__ import annotations

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


def test_cn_endpoint_caps_max_completion_tokens_at_2048():
    captured = _capture_payload("https://api.minimaxi.com", 4096)
    assert captured["url"] == "https://api.minimaxi.com/v1/chat/completions"
    assert captured["payload"]["max_completion_tokens"] == 2048


def test_cn_endpoint_preserves_lower_max_completion_tokens():
    captured = _capture_payload("https://api.minimaxi.com/v1", 1024)
    assert captured["payload"]["max_completion_tokens"] == 1024


def test_global_endpoint_keeps_existing_4096_default_behavior():
    captured = _capture_payload("https://api.minimax.io", 4096)
    assert captured["url"] == "https://api.minimax.io/v1/chat/completions"
    assert captured["payload"]["max_completion_tokens"] == 4096
