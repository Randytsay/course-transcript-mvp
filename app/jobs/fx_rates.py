"""Small, auditable USD/TWD exchange-rate resolver.

The application keeps USD as its accounting source of truth.  This module
only resolves the rate used for the user-facing TWD estimate and stores a
bounded local cache so a temporary public-data outage cannot block a job.
"""
from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen


DEFAULT_CBC_URL = "https://cpx.cbc.gov.tw/api/OpenData/FTDOpenData_Day"
DEFAULT_CACHE_PATH = "/app/data/fx/usd-twd.json"
DEFAULT_FALLBACK_RATE = Decimal("32")
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MAX_STALE_SECONDS = 72 * 60 * 60
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class FxRate:
    rate: Decimal
    source: str
    rate_date: str | None
    fetched_at: str | None
    stale: bool
    auto_enabled: bool


HttpGet = Callable[[str, float], object]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _parse_rate(value: object) -> Decimal | None:
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return rate if rate.is_finite() and rate > 0 else None


def parse_cbc_response(payload: object) -> tuple[Decimal, str]:
    """Extract the latest NTD/USD closing rate from CBC daily data."""
    if isinstance(payload, list):
        rows = payload
        row_format = "open_data"
    elif isinstance(payload, dict):
        data = payload.get("data")
        rows = data.get("dataSets") if isinstance(data, dict) else None
        row_format = "statistical_api"
    else:
        rows = None
        row_format = "unknown"
    candidates: list[tuple[str, Decimal]] = []
    if not isinstance(rows, list):
        raise ValueError("CBC response is missing dataSets")
    for row in rows:
        if row_format == "open_data":
            if not isinstance(row, dict):
                continue
            date_value = str(row.get("日期") or row.get("date") or "").strip()
            raw_rate = row.get("NTD_USD") or row.get("ntd_usd")
        else:
            if not isinstance(row, list) or len(row) < 2:
                continue
            date_value = str(row[0]).strip()
            raw_rate = row[1]
        try:
            parsed_date = datetime.strptime(date_value, "%Y%m%d").date()
        except ValueError:
            continue
        rate = _parse_rate(raw_rate)
        if rate is not None:
            candidates.append((parsed_date.isoformat(), rate))
    if not candidates:
        raise ValueError("CBC response has no valid NTD/USD observations")
    rate_date, rate = max(candidates, key=lambda item: item[0])
    return rate, rate_date


def _default_http_get(url: str, timeout: float) -> object:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "course-transcript-mvp/fx-rate",
        },
    )
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    # Python 3.14 enables VERIFY_X509_STRICT by default; the CBC endpoint's
    # otherwise-valid chain omits a legacy Subject Key Identifier. Keep normal
    # certificate and hostname verification while allowing this endpoint.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    with urlopen(  # nosec B310: fixed HTTPS default
        request,
        timeout=timeout,
        context=context,
    ) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("CBC response is too large")
    return json.loads(raw.decode("utf-8"))


def _read_cache(path: Path) -> tuple[Decimal, str, datetime] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    rate = _parse_rate(payload.get("rate"))
    rate_date = payload.get("rate_date")
    fetched_at = _parse_timestamp(payload.get("fetched_at"))
    if rate is None or not isinstance(rate_date, str) or fetched_at is None:
        return None
    return rate, rate_date, fetched_at


def _write_cache(path: Path, *, rate: Decimal, rate_date: str, fetched_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "rate": str(rate),
                "rate_date": rate_date,
                "fetched_at": fetched_at.isoformat(),
                "source": "cbc_ntd_usd_closing",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def resolve_usd_to_twd(
    *,
    auto_enabled: bool,
    fallback_rate: Decimal = DEFAULT_FALLBACK_RATE,
    cache_path: Path | None = None,
    url: str = DEFAULT_CBC_URL,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    max_stale_seconds: int = DEFAULT_MAX_STALE_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
    http_get: HttpGet | None = None,
) -> FxRate:
    """Resolve a rate with fresh-cache, bounded-stale-cache, then fallback order."""
    if fallback_rate <= 0:
        raise ValueError("fallback_rate must be positive")
    if not auto_enabled:
        return FxRate(
            rate=fallback_rate,
            source="configured_manual",
            rate_date=None,
            fetched_at=None,
            stale=False,
            auto_enabled=False,
        )

    current = (now or _utc_now()).astimezone(UTC)
    path = cache_path or Path(
        os.environ.get("COURSE_TRANSCRIPT_FX_CACHE_PATH", DEFAULT_CACHE_PATH)
    )
    cached = _read_cache(path)
    if cached is not None:
        cached_rate, cached_date, cached_fetched_at = cached
        cache_age = max(0.0, (current - cached_fetched_at).total_seconds())
        if cache_age <= max(0, cache_ttl_seconds):
            return FxRate(
                rate=cached_rate,
                source="cbc_ntd_usd_closing_cache",
                rate_date=cached_date,
                fetched_at=cached_fetched_at.isoformat(),
                stale=False,
                auto_enabled=True,
            )

    try:
        payload = (http_get or _default_http_get)(url, timeout_seconds)
        rate, rate_date = parse_cbc_response(payload)
        fetched_at = current
        _write_cache(path, rate=rate, rate_date=rate_date, fetched_at=fetched_at)
        return FxRate(
            rate=rate,
            source="cbc_ntd_usd_closing",
            rate_date=rate_date,
            fetched_at=fetched_at.isoformat(),
            stale=False,
            auto_enabled=True,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        if cached is not None:
            cached_rate, cached_date, cached_fetched_at = cached
            cache_age = max(0.0, (current - cached_fetched_at).total_seconds())
            if cache_age <= max(0, max_stale_seconds):
                return FxRate(
                    rate=cached_rate,
                    source="cbc_ntd_usd_closing_cache",
                    rate_date=cached_date,
                    fetched_at=cached_fetched_at.isoformat(),
                    stale=True,
                    auto_enabled=True,
                )
        return FxRate(
            rate=fallback_rate,
            source="configured_fallback",
            rate_date=None,
            fetched_at=None,
            stale=True,
            auto_enabled=True,
        )


def resolve_from_env(manual_rate: Decimal) -> FxRate:
    auto_enabled = os.environ.get("COURSE_TRANSCRIPT_FX_AUTO_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return resolve_usd_to_twd(
        auto_enabled=auto_enabled,
        fallback_rate=manual_rate,
        cache_path=Path(
            os.environ.get(
                "COURSE_TRANSCRIPT_FX_CACHE_PATH",
                Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
                / "fx/usd-twd.json",
            )
        ),
        url=os.environ.get("COURSE_TRANSCRIPT_FX_URL", DEFAULT_CBC_URL),
        cache_ttl_seconds=int(
            os.environ.get("COURSE_TRANSCRIPT_FX_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
        ),
        max_stale_seconds=int(
            os.environ.get("COURSE_TRANSCRIPT_FX_MAX_STALE_SECONDS", str(DEFAULT_MAX_STALE_SECONDS))
        ),
        timeout_seconds=float(
            os.environ.get("COURSE_TRANSCRIPT_FX_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        ),
    )
