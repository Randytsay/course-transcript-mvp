"""Shared production-hardening helpers for asynchronous provider work."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PENDING_EXIT = 75
RETRYABLE_EXIT = 76
TERMINAL_EXIT = 78


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def env_true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def provider_deadline() -> timedelta:
    seconds = max(
        3_600,
        int(os.environ.get("CHIRP_PROVIDER_DEADLINE_SECONDS", "90000")),
    )
    return timedelta(seconds=seconds)


def propagation_grace() -> timedelta:
    seconds = max(
        30,
        int(os.environ.get("CHIRP_OUTPUT_PROPAGATION_GRACE_SECONDS", "300")),
    )
    return timedelta(seconds=seconds)


def window_matches(
    manifest: dict[str, Any],
    *,
    start_seconds: float,
    end_seconds: float,
    dynamic_batching: bool,
    tolerance_ms: int = 50,
) -> bool:
    try:
        start_ms = int(manifest["source_start_ms"])
        end_ms = int(manifest["source_end_ms"])
    except (KeyError, TypeError, ValueError):
        return False
    expected_strategy = (
        "DYNAMIC_BATCHING"
        if dynamic_batching
        else "PROCESSING_STRATEGY_UNSPECIFIED"
    )
    strategy = str(manifest.get("processing_strategy") or expected_strategy)
    return (
        abs(start_ms - round(start_seconds * 1000)) <= tolerance_ms
        and abs(end_ms - round(end_seconds * 1000)) <= tolerance_ms
        and strategy == expected_strategy
    )


def retry_delay_seconds(transient_errors: int, base_seconds: int = 120) -> int:
    schedule = (base_seconds, 300, 600, 1_800, 3_600)
    index = max(0, min(transient_errors - 1, len(schedule) - 1))
    return schedule[index]
