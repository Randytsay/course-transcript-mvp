"""Durable scheduling for non-blocking Chirp recovery passes."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from app.providers.hardening_common import atomic_json, iso, parse_time, retry_delay_seconds, utcnow

Outcome = Literal["submitted", "pending", "retryable", "completed", "terminal"]


def path_for(job_dir: Path) -> Path:
    return job_dir / "chirp-recovery-state.json"


def load(job_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path_for(job_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def is_due(job_dir: Path, now: datetime | None = None) -> bool:
    state = load(job_dir)
    next_at = parse_time(state.get("next_recovery_at"))
    return next_at is None or next_at <= (now or utcnow())


def schedule(
    job_dir: Path,
    outcome: Outcome,
    *,
    detail: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = load(job_dir)
    timestamp = now or utcnow()
    transient_errors = int(current.get("transient_errors", 0))
    poll_seconds = max(30, int(os.environ.get("CHIRP_RECOVERY_POLL_SECONDS", "120")))
    if outcome == "retryable":
        transient_errors += 1
        delay = retry_delay_seconds(transient_errors, poll_seconds)
        next_at = timestamp + timedelta(seconds=delay)
    elif outcome in {"submitted", "pending"}:
        if outcome == "pending":
            transient_errors = 0
        next_at = timestamp + timedelta(seconds=poll_seconds)
    else:
        next_at = None
    state = {
        **current,
        "last_outcome": outcome,
        "last_detail": detail,
        "last_checked_at": iso(timestamp),
        "transient_errors": transient_errors,
        "next_recovery_at": iso(next_at) if next_at else None,
    }
    atomic_json(path_for(job_dir), state)
    return state
