from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from app.jobs.fx_rates import parse_cbc_response, resolve_usd_to_twd


def cbc_payload() -> dict[str, object]:
    return {
        "data": {
            "dataSets": [
                ["20260813", "32.168", "ignored"],
                ["20260814", "32.046", "ignored"],
                ["bad-date", "not-a-rate"],
            ]
        }
    }


def cbc_open_data_payload() -> list[dict[str, str]]:
    return [
        {"日期": "20260813", "NTD_USD": "32.168"},
        {"日期": "20260814", "NTD_USD": "32.046"},
        {"日期": "bad-date", "NTD_USD": "not-a-rate"},
    ]


class FxRateTests(unittest.TestCase):
    def test_parse_cbc_response_uses_latest_valid_ntd_usd_row(self) -> None:
        self.assertEqual(parse_cbc_response(cbc_payload()), (Decimal("32.046"), "2026-08-14"))
        self.assertEqual(
            parse_cbc_response(cbc_open_data_payload()),
            (Decimal("32.046"), "2026-08-14"),
        )

    def test_fresh_auto_rate_is_cached_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "usd-twd.json"
            now = datetime(2026, 8, 16, tzinfo=UTC)
            http_get = Mock(return_value=cbc_payload())
            first = resolve_usd_to_twd(
                auto_enabled=True,
                fallback_rate=Decimal("32"),
                cache_path=cache,
                now=now,
                http_get=http_get,
            )
            second = resolve_usd_to_twd(
                auto_enabled=True,
                fallback_rate=Decimal("32"),
                cache_path=cache,
                now=now + timedelta(hours=12),
                http_get=Mock(side_effect=AssertionError("fresh cache must not fetch")),
            )

        self.assertEqual(first.rate, Decimal("32.046"))
        self.assertEqual(first.source, "cbc_ntd_usd_closing")
        self.assertFalse(first.stale)
        self.assertEqual(second.rate, Decimal("32.046"))
        self.assertEqual(second.source, "cbc_ntd_usd_closing_cache")

    def test_failed_refresh_uses_bounded_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "usd-twd.json"
            fetched_at = datetime(2026, 8, 14, tzinfo=UTC)
            cache.write_text(
                json.dumps(
                    {
                        "rate": "32.046",
                        "rate_date": "2026-08-14",
                        "fetched_at": fetched_at.isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            result = resolve_usd_to_twd(
                auto_enabled=True,
                fallback_rate=Decimal("32"),
                cache_path=cache,
                now=fetched_at + timedelta(hours=48),
                http_get=Mock(side_effect=OSError("network down")),
            )

        self.assertEqual(result.rate, Decimal("32.046"))
        self.assertTrue(result.stale)
        self.assertEqual(result.source, "cbc_ntd_usd_closing_cache")

    def test_failed_refresh_uses_manual_fallback_after_stale_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "usd-twd.json"
            fetched_at = datetime(2026, 8, 10, tzinfo=UTC)
            cache.write_text(
                json.dumps(
                    {
                        "rate": "32.046",
                        "rate_date": "2026-08-08",
                        "fetched_at": fetched_at.isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            result = resolve_usd_to_twd(
                auto_enabled=True,
                fallback_rate=Decimal("32"),
                cache_path=cache,
                now=datetime(2026, 8, 16, tzinfo=UTC),
                http_get=Mock(side_effect=OSError("network down")),
            )

        self.assertEqual(result.rate, Decimal("32"))
        self.assertTrue(result.stale)
        self.assertEqual(result.source, "configured_fallback")

    def test_manual_mode_does_not_call_network(self) -> None:
        result = resolve_usd_to_twd(
            auto_enabled=False,
            fallback_rate=Decimal("31.8"),
            http_get=Mock(side_effect=AssertionError("manual mode must not fetch")),
        )
        self.assertEqual(result.rate, Decimal("31.8"))
        self.assertEqual(result.source, "configured_manual")
        self.assertFalse(result.stale)


if __name__ == "__main__":
    unittest.main()
