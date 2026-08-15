from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.correction_routing import M3QuotaState
from app.providers.minimax_quota import MiniMaxQuotaClient, parse_quota_response


def old_shape(**overrides: object) -> dict[str, object]:
    model = {
        "model_name": "MiniMax-M3",
        "current_interval_total_count": 100,
        "current_interval_usage_count": 20,
        "current_weekly_total_count": 1000,
        "current_weekly_usage_count": 100,
        "start_time": 1_770_000_000_000,
        "end_time": 1_770_018_000_000,
        "weekly_start_time": 1_769_000_000_000,
        "weekly_end_time": 1_775_000_000_000,
    }
    model.update(overrides)
    return {"model_remains": [model], "base_resp": {"status_code": 0}}


class MiniMaxQuotaNormalizationTests(unittest.TestCase):
    def test_old_count_based_m3_available(self) -> None:
        snapshot = parse_quota_response(old_shape())
        self.assertEqual(snapshot.state, M3QuotaState.AVAILABLE)
        self.assertEqual(snapshot.source_pool, "MiniMax-M3")
        self.assertEqual(snapshot.interval_remaining, 80)
        self.assertEqual(snapshot.weekly_remaining, 900)

    def test_old_count_based_m_star_alias_is_supported(self) -> None:
        snapshot = parse_quota_response(
            {"model_remains": [{**old_shape()["model_remains"][0], "model_name": "MiniMax-M*"}]}
        )
        self.assertEqual(snapshot.state, M3QuotaState.AVAILABLE)

    def test_old_interval_exhausted(self) -> None:
        snapshot = parse_quota_response(old_shape(current_interval_usage_count=100))
        self.assertEqual(snapshot.state, M3QuotaState.UNAVAILABLE)

    def test_old_weekly_exhausted(self) -> None:
        snapshot = parse_quota_response(old_shape(current_weekly_usage_count=1000))
        self.assertEqual(snapshot.state, M3QuotaState.UNAVAILABLE)

    def test_new_general_percentage_available(self) -> None:
        payload = {
            "model_remains": [
                {
                    "model_name": "general",
                    "current_interval_remaining_percent": 82.5,
                    "current_weekly_remaining_percent": 61.0,
                    "end_time": 1_770_018_000_000,
                    "weekly_end_time": 1_775_000_000_000,
                }
            ],
            "base_resp": {"status_code": 0},
        }
        snapshot = parse_quota_response(payload)
        self.assertEqual(snapshot.state, M3QuotaState.AVAILABLE)
        self.assertEqual(snapshot.source_pool, "general")
        self.assertEqual(snapshot.interval_unit, "percent")

    def test_live_cn_general_and_video_shape_uses_general_text_pool(self) -> None:
        # Sanitized shape captured from the validated CN Token Plan account;
        # the video pool must never be mistaken for text/M3 availability.
        payload = {
            "base_resp": {"status_code": 0},
            "model_remains": [
                {
                    "model_name": "general",
                    "current_interval_remaining_percent": 90,
                    "current_weekly_remaining_percent": 69,
                    "start_time": 1786809600000,
                    "end_time": 1786827600000,
                    "weekly_start_time": 1786291200000,
                    "weekly_end_time": 1786896000000,
                    "current_interval_total_count": 0,
                    "current_interval_usage_count": 0,
                    "current_weekly_total_count": 0,
                    "current_weekly_usage_count": 0,
                },
                {
                    "model_name": "video",
                    "current_interval_remaining_percent": 100,
                    "current_weekly_remaining_percent": 100,
                },
            ],
        }
        snapshot = parse_quota_response(payload)
        self.assertEqual(snapshot.state, M3QuotaState.AVAILABLE)
        self.assertEqual(snapshot.source_pool, "general")
        self.assertEqual(snapshot.interval_remaining, 90)
        self.assertEqual(snapshot.weekly_remaining, 69)
        self.assertEqual(snapshot.interval_unit, "percent")

    def test_new_general_interval_exhausted(self) -> None:
        snapshot = parse_quota_response(
            {
                "model_remains": [
                    {
                        "model_name": "general",
                        "current_interval_remaining_percent": 0,
                        "current_weekly_remaining_percent": 61,
                    }
                ]
            }
        )
        self.assertEqual(snapshot.state, M3QuotaState.UNAVAILABLE)

    def test_new_general_weekly_exhausted(self) -> None:
        snapshot = parse_quota_response(
            {
                "model_remains": [
                    {
                        "model_name": "general",
                        "current_interval_remaining_percent": 61,
                        "current_weekly_remaining_percent": 0,
                    }
                ]
            }
        )
        self.assertEqual(snapshot.state, M3QuotaState.UNAVAILABLE)

    def test_unknown_pool_missing_fields_and_malformed_are_unknown(self) -> None:
        self.assertEqual(
            parse_quota_response({"model_remains": [{"model_name": "renamed-text"}]}).state,
            M3QuotaState.UNKNOWN,
        )
        self.assertEqual(parse_quota_response(old_shape(current_weekly_usage_count=None)).state, M3QuotaState.UNKNOWN)
        self.assertEqual(parse_quota_response("not-json").state, M3QuotaState.UNKNOWN)

    def test_network_error_and_stale_cache_never_assume_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "key"
            key_file.write_text("test-secret", encoding="utf-8")
            now = [0.0]
            calls: list[int] = []

            def http_get(url: str, headers: object, timeout: float) -> tuple[int, dict[str, str], bytes]:
                calls.append(1)
                if len(calls) == 1:
                    return 200, {}, json.dumps(old_shape()).encode()
                raise OSError("network down")

            client = MiniMaxQuotaClient(
                key_file=key_file,
                ttl_seconds=5,
                http_get=http_get,
                clock=lambda: now[0],
            )
            self.assertEqual(client.get_quota(force_refresh=True).state, M3QuotaState.AVAILABLE)
            now[0] = 6
            self.assertEqual(client.get_quota().state, M3QuotaState.UNKNOWN)
            self.assertEqual(len(calls), 2)

    def test_exhausted_response_can_be_seen_immediately_with_force_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "key"
            key_file.write_text("test-secret", encoding="utf-8")
            payloads = [old_shape(), old_shape(current_interval_usage_count=100)]

            def http_get(url: str, headers: object, timeout: float) -> tuple[int, dict[str, str], bytes]:
                return 200, {}, json.dumps(payloads.pop(0)).encode()

            client = MiniMaxQuotaClient(key_file=key_file, http_get=http_get)
            self.assertEqual(client.get_quota(force_refresh=True).state, M3QuotaState.AVAILABLE)
            self.assertEqual(client.get_quota(force_refresh=True).state, M3QuotaState.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
