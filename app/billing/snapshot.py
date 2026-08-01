"""Atomic persistence and safe API shaping for billing snapshots."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|credential|private[_ -]?key|access[_ -]?token)\s*[:=]\s*\S+"
)
_PATH_PATTERN = re.compile(r"(?:/[^\s:]+){2,}")
_GCS_PATTERN = re.compile(r"gs://[^\s]+")


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def safe_error(value: object) -> str:
    text = str(value)
    text = _SECRET_PATTERN.sub(r"\1=[REDACTED]", text)
    text = _GCS_PATTERN.sub("gs://[REDACTED]", text)
    text = _PATH_PATTERN.sub("/[REDACTED]", text)
    return text[-400:]


def write_disabled(path: Path) -> dict[str, Any]:
    payload = {
        "status": "disabled",
        "source": "bigquery_standard_billing_export",
        "warning": "尚未設定 Cloud Billing BigQuery 匯出",
        "lastBillingDataAt": None,
        "snapshotGeneratedAt": iso_now(),
        "dataAgeSeconds": None,
    }
    atomic_write_json(path, payload)
    return payload


def preserve_previous_failure(path: Path, error: object) -> dict[str, Any]:
    previous = read_snapshot(path)
    now = iso_now()
    if previous and previous.get("status") in {"ok", "stale"}:
        previous["status"] = "stale"
        previous["lastError"] = safe_error(error)
        previous["lastAttemptAt"] = now
        previous["warning"] = (
            "帳務同步失敗，顯示上一份成功資料；官方帳務請以 Billing Overview 為準。"
        )
        atomic_write_json(path, previous)
        return previous
    payload = {
        "status": "error",
        "source": "bigquery_standard_billing_export",
        "warning": "帳務同步目前無法完成",
        "lastError": safe_error(error),
        "lastAttemptAt": now,
        "lastBillingDataAt": None,
        "snapshotGeneratedAt": now,
        "dataAgeSeconds": None,
    }
    atomic_write_json(path, payload)
    return payload


def snapshot_for_api(
    path: Path,
    *,
    enabled: bool,
    stale_seconds: int,
) -> dict[str, Any]:
    if not enabled:
        return {
            "status": "disabled",
            "source": "bigquery_standard_billing_export",
            "warning": "尚未設定 Cloud Billing BigQuery 匯出",
            "lastBillingDataAt": None,
            "snapshotGeneratedAt": None,
            "dataAgeSeconds": None,
        }
    payload = read_snapshot(path)
    if payload is None:
        return {
            "status": "pending",
            "source": "bigquery_standard_billing_export",
            "warning": "帳務同步尚未產生第一份資料",
            "lastBillingDataAt": None,
            "snapshotGeneratedAt": None,
            "dataAgeSeconds": None,
        }
    generated = payload.get("snapshotGeneratedAt")
    try:
        generated_at = datetime.fromisoformat(str(generated))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        age_seconds = max(0, int((utc_now() - generated_at).total_seconds()))
    except (TypeError, ValueError):
        age_seconds = None
    result = dict(payload)
    result["dataAgeSeconds"] = age_seconds
    if (
        age_seconds is not None
        and age_seconds > stale_seconds
        and result.get("status") == "ok"
    ):
        result["status"] = "stale"
        result["warning"] = (
            "帳務資料已超過設定時間未更新；官方帳務請以 Billing Overview 為準。"
        )
    return result
