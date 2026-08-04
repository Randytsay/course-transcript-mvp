"""Read-only production health report for the transcription service.

This command never calls Chirp, Gemini, Google Drive, or Cloud Billing. It reads
local SQLite/job evidence and emits a deterministic report suitable for cron,
Uptime Kuma, systemd timers, or an authenticated admin endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(0.0, value)


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    job_id: str | None = None
    age_hours: float | None = None


def _hours_since(value: Any, *, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600)


def _dynamic_batch_findings(
    row: sqlite3.Row,
    *,
    now: datetime,
    warning_hours: float,
    critical_hours: float,
    breach_hours: float,
) -> list[Finding]:
    if row["status"] != "transcribing" or row["active_stage"] != "chirp":
        return []
    detail = str(row["stage_detail"] or "")
    if "動態批次" not in detail and "離峰" not in detail:
        return []
    age = _hours_since(row["updated_at"], now=now)
    if age is None or age < warning_hours:
        return []
    if age >= breach_hours:
        severity, code = "critical", "dynamic_batch_sla_breach"
        message = "Dynamic Batch 已超過 24 小時營運門檻；禁止自動重送，需人工檢查 operation 與帳單。"
    elif age >= critical_hours:
        severity, code = "critical", "dynamic_batch_near_sla"
        message = "Dynamic Batch 接近 24 小時門檻；請優先檢查 operation 狀態。"
    else:
        severity, code = "warning", "dynamic_batch_delayed"
        message = "Dynamic Batch 等待時間偏長，但仍在 24 小時營運門檻內。"
    return [Finding(severity, code, message, str(row["id"]), round(age, 2))]


def _drive_delivery_findings(job_dir: Path, job_id: str) -> list[Finding]:
    path = job_dir / "drive-delivery-state.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [
            Finding(
                "warning",
                "drive_delivery_state_unreadable",
                "Drive 延遲回寫狀態檔無法讀取。",
                job_id,
            )
        ]
    status = str(payload.get("status") or "")
    if status in {"pending_retry", "editor_publish_failed"}:
        attempts = int(payload.get("attempts") or 0)
        severity = "critical" if attempts >= 5 else "warning"
        return [
            Finding(
                severity,
                "drive_delivery_pending",
                f"Drive 回寫待重試，累計 attempts={attempts}；付費辨識不可重跑。",
                job_id,
            )
        ]
    return []


def build_report(
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utcnow()
    database = data_dir / "course-transcript.db"
    findings: list[Finding] = []
    counts: dict[str, int] = {
        "jobs": 0,
        "active": 0,
        "failed": 0,
        "dynamic_waiting": 0,
        "drive_pending": 0,
    }
    if not database.is_file():
        findings.append(
            Finding("critical", "database_missing", f"找不到資料庫：{database}")
        )
        return _finalize(data_dir, now, counts, findings)

    warning_hours = _env_float("DYNAMIC_BATCH_WARNING_HOURS", 18)
    critical_hours = _env_float("DYNAMIC_BATCH_CRITICAL_HOURS", 23)
    breach_hours = _env_float("DYNAMIC_BATCH_SLA_HOURS", 24)
    if not (warning_hours <= critical_hours <= breach_hours):
        findings.append(
            Finding(
                "critical",
                "invalid_dynamic_batch_thresholds",
                "離峰告警門檻必須符合 warning <= critical <= sla。",
            )
        )

    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, status, active_stage, stage_detail, error, updated_at
            FROM jobs
            ORDER BY updated_at DESC
            """
        ).fetchall()
    except sqlite3.Error as exc:
        findings.append(
            Finding("critical", "database_query_failed", f"資料庫查詢失敗：{type(exc).__name__}")
        )
        rows = []
    finally:
        connection.close()

    active_statuses = {
        "preflight",
        "awaiting_confirmation",
        "queued",
        "downloading",
        "normalizing",
        "transcribing",
        "merging",
        "segmenting",
        "correcting",
        "exporting",
        "quality_check",
    }
    for row in rows:
        job_id = str(row["id"])
        counts["jobs"] += 1
        if row["status"] in active_statuses:
            counts["active"] += 1
        if row["status"] == "failed":
            counts["failed"] += 1
            findings.append(
                Finding(
                    "warning",
                    "job_failed",
                    f"任務失敗：{str(row['error'] or '未提供錯誤')[:300]}",
                    job_id,
                )
            )
        dynamic = _dynamic_batch_findings(
            row,
            now=now,
            warning_hours=warning_hours,
            critical_hours=critical_hours,
            breach_hours=breach_hours,
        )
        if row["status"] == "transcribing" and row["active_stage"] == "chirp" and (
            "動態批次" in str(row["stage_detail"] or "")
            or "離峰" in str(row["stage_detail"] or "")
        ):
            counts["dynamic_waiting"] += 1
        findings.extend(dynamic)
        drive_findings = _drive_delivery_findings(data_dir / "jobs" / job_id, job_id)
        counts["drive_pending"] += sum(
            item.code == "drive_delivery_pending" for item in drive_findings
        )
        findings.extend(drive_findings)

    return _finalize(data_dir, now, counts, findings)


def _finalize(
    data_dir: Path,
    now: datetime,
    counts: dict[str, int],
    findings: list[Finding],
) -> dict[str, Any]:
    rank = {"ok": 0, "warning": 1, "critical": 2}
    status = "ok"
    for finding in findings:
        if rank[finding.severity] > rank[status]:
            status = finding.severity
    return {
        "status": status,
        "generated_at": now.isoformat(),
        "data_dir": str(data_dir),
        "counts": counts,
        "findings": [asdict(item) for item in findings],
    }


def _print_human(report: dict[str, Any]) -> None:
    counts = report["counts"]
    print(
        "PRODUCTION_HEALTH=" + str(report["status"]).upper(),
        f"jobs={counts['jobs']}",
        f"active={counts['active']}",
        f"failed={counts['failed']}",
        f"dynamic_waiting={counts['dynamic_waiting']}",
        f"drive_pending={counts['drive_pending']}",
    )
    for finding in report["findings"]:
        suffix = f" job={finding['job_id']}" if finding.get("job_id") else ""
        age = (
            f" age_hours={finding['age_hours']}"
            if finding.get("age_hours") is not None
            else ""
        )
        print(
            f"{finding['severity'].upper()} {finding['code']}{suffix}{age}: "
            f"{finding['message']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    report = build_report(args.data_dir)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return {"ok": 0, "warning": 1, "critical": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
