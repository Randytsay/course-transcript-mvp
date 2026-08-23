"""Focused tests for the redesigned MiniMax M3 realtime correction path.

All provider calls are mocked.  These tests verify the behavior that replaces
PR #50's one-way course-level fallback without making any paid request.
"""
from __future__ import annotations

import json
import unittest

from app.providers.correction.base import ProviderError
from app.providers.correction.minimax import MiniMaxCorrectionProvider
from app.providers.correction.orchestrator import (
    CorrectionOrchestrator,
    JobCorrectionSpec,
    build_realtime_windows,
)


def segments(count: int, *, text_size: int = 8) -> list[dict]:
    return [
        {"segment_id": f"s{i:03d}", "text": (f"第{i}段" + "字" * text_size)}
        for i in range(count)
    ]


def spec() -> JobCorrectionSpec:
    return JobCorrectionSpec(
        job_id="job-1",
        provider="minimax",
        provider_profile_id="mm-main",
        model="MiniMax-M3",
        execution_mode="REALTIME",
        fallback_policy="RAW_CHIRP_FALLBACK",
    )


def corrected_for_prompt(prompt: str) -> str:
    payload = json.loads(prompt.split("\n\n", 1)[1])
    return json.dumps([
        {
            "segment_id": item["segment_id"],
            "corrected_text": item["text"] + "校",
            "uncertain_terms": [],
        }
        for item in payload["segments"]
    ], ensure_ascii=False)


class TestMiniMaxProviderRequest(unittest.TestCase):
    def test_deterministic_payload_does_not_request_json_object(self):
        captured = []

        def http(method, url, headers, payload=None):
            captured.append((method, url, payload))
            return 200, {
                "choices": [{
                    "message": {"content": "[]"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }

        provider = MiniMaxCorrectionProvider(api_key="fake", http=http)
        assert provider.realtime_generate("x") == "[]"
        request = captured[0][2]
        assert request["thinking"] == {"type": "disabled"}
        assert request["reasoning_split"] is True
        assert request["max_completion_tokens"] == 4096
        assert "response_format" not in request
        assert provider.last_response_meta["finish_reason"] == "stop"
        assert isinstance(provider.last_response_meta["usage"], dict)

    def test_finish_reason_length_is_never_accepted(self):
        def http(method, url, headers, payload=None):
            return 200, {
                "choices": [{
                    "message": {"content": "[]"},
                    "finish_reason": "length",
                }],
                "usage": {},
            }

        provider = MiniMaxCorrectionProvider(api_key="fake", http=http)
        with self.assertRaises(ProviderError) as cm:
            provider.realtime_generate("x")
        assert cm.exception.kind == "output_limit"

    def test_422_keeps_only_safe_provider_code(self):
        def http(method, url, headers, payload=None):
            return 422, {
                "error": {
                    "code": "bad_parameter",
                    "message": "SECRET provider response text must not leak",
                }
            }

        provider = MiniMaxCorrectionProvider(api_key="fake", http=http)
        with self.assertRaises(ProviderError) as cm:
            provider.realtime_generate("x")
        assert cm.exception.kind == "invalid_request"
        assert "bad_parameter" in cm.exception.safe_message
        assert "SECRET" not in cm.exception.safe_message


class TestRealtimeWindowBuilder(unittest.TestCase):
    def test_segment_limit_splits_deterministically(self):
        windows = build_realtime_windows(segments(49))
        assert [len(w["segments"]) for w in windows] == [24, 24, 1]
        assert windows[0]["window_id"].endswith("s000..s023")
        assert windows[-1]["window_id"].endswith("s048..s048")

    def test_char_limit_can_split_before_segment_limit(self):
        windows = build_realtime_windows(
            segments(6, text_size=100), max_segments=24, max_chars=250
        )
        assert len(windows) >= 3
        assert all(w["char_count"] <= 250 or len(w["segments"]) == 1 for w in windows)


class TestMiniMaxWindowFallback(unittest.TestCase):
    def test_one_bad_window_falls_back_and_later_windows_still_use_m3(self):
        calls = 0

        class Client:
            last_response_meta = {"finish_reason": "stop", "usage": {}}

            def realtime_generate(self, prompt):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ProviderError("invalid_request", "window-specific 422")
                return corrected_for_prompt(prompt)

        orch = CorrectionOrchestrator(
            run_store=None, client_factory=lambda provider, profile: Client()
        )
        source = segments(49)
        result = orch.correct_realtime(spec(), source, [])

        assert calls == 3
        assert len(result["corrections"]) == 49
        assert result["provider_circuit_opened"] is False
        assert len(result["fallback_segment_ids"]) == 24
        assert result["fallback_segment_ids"][0] == "s024"
        assert result["fallback_segment_ids"][-1] == "s047"
        # First and third windows were corrected; only the failed middle window
        # remains exact Chirp text.
        by_id = {item["segment_id"]: item for item in result["corrections"]}
        assert by_id["s000"]["corrected_text"].endswith("校")
        assert by_id["s024"]["corrected_text"] == source[24]["text"]
        assert by_id["s048"]["corrected_text"].endswith("校")

    def test_invalid_json_gets_one_bounded_retry(self):
        calls = 0

        class Client:
            last_response_meta = {"finish_reason": "stop", "usage": {}}

            def realtime_generate(self, prompt):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return "not-json"
                return corrected_for_prompt(prompt)

        orch = CorrectionOrchestrator(
            run_store=None, client_factory=lambda provider, profile: Client()
        )
        result = orch.correct_realtime(spec(), segments(2), [])
        assert calls == 2
        assert result["fallback_segment_ids"] == []
        assert result["window_results"][0]["attempts"] == 2

    def test_three_transport_failed_windows_open_provider_circuit(self):
        calls = 0

        class Client:
            def realtime_generate(self, prompt):
                nonlocal calls
                calls += 1
                raise ProviderError("unreachable", "temporary outage")

        orch = CorrectionOrchestrator(
            run_store=None, client_factory=lambda provider, profile: Client()
        )
        source = segments(96)  # four 24-segment windows
        result = orch.correct_realtime(spec(), source, [])

        # 3 failed windows * 2 bounded attempts. The fourth is not sent after
        # the circuit opens.
        assert calls == 6
        assert result["provider_circuit_opened"] is True
        assert len(result["fallback_segment_ids"]) == 96
        assert result["window_results"][-1]["reason"] == "provider_circuit_open"

    def test_auth_failure_is_course_fatal_not_hidden_by_raw_fallback(self):
        calls = 0

        class Client:
            def realtime_generate(self, prompt):
                nonlocal calls
                calls += 1
                raise ProviderError("auth", "invalid credential")

        orch = CorrectionOrchestrator(
            run_store=None, client_factory=lambda provider, profile: Client()
        )
        with self.assertRaises(ProviderError) as cm:
            orch.correct_realtime(spec(), segments(30), [])
        assert calls == 1
        assert cm.exception.kind == "auth"


if __name__ == "__main__":
    unittest.main()
