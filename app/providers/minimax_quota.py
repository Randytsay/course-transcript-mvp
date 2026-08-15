"""Safe MiniMax Token Plan quota normalization for model routing.

The dashboard repository has observed two response families: model-specific
count fields (``MiniMax-M3``/``MiniMax-M*``) and a shared ``general`` pool with
remaining percentages.  This module copies only that parsing knowledge.  It
never returns mock data and never stores the provider response or credentials.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.providers.correction_routing import M3QuotaState


# The validated Token Plan account is on the CN endpoint. Keep the endpoint
# explicit so an omitted protected env override cannot silently use global.
DEFAULT_QUOTA_URL = "https://api.minimaxi.com/v1/token_plan/remains"
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


@dataclass(frozen=True)
class MiniMaxQuotaSnapshot:
    state: M3QuotaState
    checked_at: str
    source_pool: str | None = None
    interval_remaining: float | int | None = None
    weekly_remaining: float | int | None = None
    interval_reset_at: str | None = None
    weekly_reset_at: str | None = None
    interval_unit: str | None = None
    weekly_unit: str | None = None
    reason: str | None = None
    http_status: int | None = None

    def safe_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "checked_at": self.checked_at,
            "source_pool": self.source_pool,
            "interval_remaining": self.interval_remaining,
            "weekly_remaining": self.weekly_remaining,
            "interval_reset_at": self.interval_reset_at,
            "weekly_reset_at": self.weekly_reset_at,
            "interval_unit": self.interval_unit,
            "weekly_unit": self.weekly_unit,
            "reason": self.reason,
            "http_status": self.http_status,
        }


def _checked_at() -> str:
    return datetime.now(UTC).isoformat()


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not _NUMBER_RE.fullmatch(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _timestamp(value: object) -> str | None:
    number = _number(value)
    if number is not None:
        # quota-dashboard observed milliseconds since epoch; accept seconds
        # too, but do not guess at arbitrary small numbers.
        seconds = number / 1000 if number >= 100_000_000_000 else number
        if seconds < 1_000_000_000 or seconds > 4_102_444_800:
            return None
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    return None


def _window_value(
    candidate: Mapping[str, Any],
    *,
    window: str,
) -> tuple[float, str] | None:
    if window == "interval":
        remaining_keys = (
            "current_interval_remaining_count",
            "current_interval_remaining",
            "interval_remaining",
            "remaining_count",
        )
        percent_keys = ("current_interval_remaining_percent", "interval_remaining_percent")
        total_key = "current_interval_total_count"
        used_key = "current_interval_usage_count"
    else:
        remaining_keys = (
            "current_weekly_remaining_count",
            "current_weekly_remaining",
            "weekly_remaining",
            "remaining_weekly_count",
        )
        percent_keys = ("current_weekly_remaining_percent", "weekly_remaining_percent")
        total_key = "current_weekly_total_count"
        used_key = "current_weekly_usage_count"

    for key in percent_keys:
        value = _number(candidate.get(key))
        if value is not None and 0 <= value <= 100:
            return value, "percent"
    for key in remaining_keys:
        value = _number(candidate.get(key))
        if value is not None and value >= 0:
            return value, "count"
    total = _number(candidate.get(total_key))
    used = _number(candidate.get(used_key))
    if total is not None and used is not None and total >= 0 and 0 <= used <= total:
        return total - used, "count"
    return None


def _pool_candidates(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = payload.get("model_remains")
    candidates: list[Mapping[str, Any]] = []
    if isinstance(values, list):
        candidates.extend(item for item in values if isinstance(item, Mapping))
    elif isinstance(values, Mapping):
        candidates.append(values)
    # Some API revisions expose a named pool rather than model_remains[].
    for key in ("general", "text", "token_plan"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            candidates.append({"model_name": key, **value})
    return candidates


def _is_text_pool(name: object) -> bool:
    normalized = str(name or "").strip().lower().replace("_", "-")
    return normalized in {
        "minimax-m3",
        "minimax-m3.0",
        "minimax-m*",
        "general",
    }


def parse_quota_response(
    payload: object,
    *,
    checked_at: str | None = None,
    http_status: int | None = None,
) -> MiniMaxQuotaSnapshot:
    """Normalize known count/percentage shapes without treating ambiguity as available."""
    checked = checked_at or _checked_at()
    if not isinstance(payload, Mapping):
        return MiniMaxQuotaSnapshot(
            M3QuotaState.UNKNOWN, checked, reason="response_not_object", http_status=http_status
        )
    base_resp = payload.get("base_resp")
    if isinstance(base_resp, Mapping):
        status = _number(base_resp.get("status_code"))
        if status is not None and status != 0:
            return MiniMaxQuotaSnapshot(
                M3QuotaState.UNKNOWN,
                checked,
                reason="provider_status_nonzero",
                http_status=http_status,
            )

    candidates = [item for item in _pool_candidates(payload) if _is_text_pool(item.get("model_name"))]
    if not candidates:
        return MiniMaxQuotaSnapshot(
            M3QuotaState.UNKNOWN,
            checked,
            reason="text_token_plan_pool_not_identified",
            http_status=http_status,
        )

    # Prefer an exact M3 entry when both historical aliases are present.
    candidates.sort(key=lambda item: 0 if str(item.get("model_name", "")).lower() == "minimax-m3" else 1)
    candidate = candidates[0]
    interval = _window_value(candidate, window="interval")
    weekly = _window_value(candidate, window="weekly")
    if interval is None or weekly is None:
        return MiniMaxQuotaSnapshot(
            M3QuotaState.UNKNOWN,
            checked,
            source_pool=str(candidate.get("model_name") or "") or None,
            reason="interval_or_weekly_allowance_missing",
            http_status=http_status,
        )
    interval_remaining, interval_unit = interval
    weekly_remaining, weekly_unit = weekly
    state = (
        M3QuotaState.UNAVAILABLE
        if interval_remaining <= 0 or weekly_remaining <= 0
        else M3QuotaState.AVAILABLE
    )
    return MiniMaxQuotaSnapshot(
        state,
        checked,
        source_pool=str(candidate.get("model_name") or "") or None,
        interval_remaining=interval_remaining,
        weekly_remaining=weekly_remaining,
        interval_reset_at=(
            _timestamp(candidate.get("end_time"))
            or _timestamp(candidate.get("remains_time"))
        ),
        weekly_reset_at=(
            _timestamp(candidate.get("weekly_end_time"))
            or _timestamp(candidate.get("weekly_remains_time"))
        ),
        interval_unit=interval_unit,
        weekly_unit=weekly_unit,
        reason="allowance_exhausted" if state is M3QuotaState.UNAVAILABLE else None,
        http_status=http_status,
    )


HttpGet = Callable[[str, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]


def _default_http_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, headers=dict(headers), method="GET")
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), dict(response.headers.items()), response.read()


class MiniMaxQuotaClient:
    """Short-lived, fail-closed Token Plan quota client."""

    def __init__(
        self,
        *,
        url: str | None = None,
        key_file: Path | None = None,
        ttl_seconds: float | None = None,
        http_get: HttpGet | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url or os.getenv("MINIMAX_M3_QUOTA_URL", DEFAULT_QUOTA_URL)
        self.key_file = key_file or Path(
            os.getenv("MINIMAX_API_KEY_FILE", "/run/secrets/minimax-api-key")
        )
        self.ttl_seconds = max(
            0.0,
            float(os.getenv("MINIMAX_M3_QUOTA_CACHE_SECONDS", "30"))
            if ttl_seconds is None
            else ttl_seconds,
        )
        self.http_get = http_get or _default_http_get
        self.clock = clock
        self._cached: MiniMaxQuotaSnapshot | None = None
        self._cached_at = 0.0

    def invalidate(self) -> None:
        self._cached = None
        self._cached_at = 0.0

    def _key(self) -> str:
        try:
            key = self.key_file.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return ""
        return key

    def get_quota(self, *, force_refresh: bool = False) -> MiniMaxQuotaSnapshot:
        now = self.clock()
        if (
            not force_refresh
            and self._cached is not None
            and now - self._cached_at <= self.ttl_seconds
        ):
            return self._cached
        key = self._key()
        if not key:
            snapshot = MiniMaxQuotaSnapshot(
                M3QuotaState.UNKNOWN,
                _checked_at(),
                reason="token_plan_key_unavailable",
            )
            self._cached, self._cached_at = snapshot, now
            return snapshot
        headers = {
            "Authorization": f"Bearer {key}",
            # quota-dashboard uses both headers for observed API compatibility.
            "x-api-key": key,
            "Accept": "application/json",
        }
        try:
            status, _response_headers, body = self.http_get(
                self.url,
                headers,
                float(os.getenv("MINIMAX_M3_QUOTA_TIMEOUT_SECONDS", "15")),
            )
            if status < 200 or status >= 300:
                snapshot = MiniMaxQuotaSnapshot(
                    M3QuotaState.UNKNOWN,
                    _checked_at(),
                    reason="quota_http_error",
                    http_status=status,
                )
                self._cached, self._cached_at = snapshot, now
                return snapshot
            payload = json.loads(body.decode("utf-8"))
            snapshot = parse_quota_response(
                payload,
                http_status=status,
            )
        except HTTPError as exc:
            snapshot = MiniMaxQuotaSnapshot(
                M3QuotaState.UNKNOWN,
                _checked_at(),
                reason=("quota_authentication_error" if exc.code in {401, 403} else "quota_http_error"),
                http_status=int(exc.code),
            )
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            snapshot = MiniMaxQuotaSnapshot(
                M3QuotaState.UNKNOWN,
                _checked_at(),
                reason="quota_fetch_or_parse_error",
            )
        self._cached, self._cached_at = snapshot, now
        return snapshot
