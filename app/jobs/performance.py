"""Durable job performance and cost observability.

The module intentionally separates application estimates from Cloud Billing.
It reads provider evidence already stored under a job directory and records
one row per top-level stage attempt without changing the existing stage_runs
contract used by the worker.
"""
from __future__ import annotations

import csv
from contextlib import closing
import html
import io
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any

from app.jobs.costs import CostConfig
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY

COMMITTED_CHUNK_STATES = {
    "SUBMITTED",
    "RUNNING",
    "RECOVERING",
    "SUCCEEDED",
    "EMPTY_SILENCE",
    "CANCEL_REQUESTED",
}
FINAL_JOB_STATES = {"awaiting_review", "review", "completed", "failed", "cancelled"}


def _connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def ensure_schema(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connection(database_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS performance_stage_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                stage TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                active_duration_ms INTEGER,
                error TEXT,
                UNIQUE(job_id, stage, attempt_number)
            );
            CREATE INDEX IF NOT EXISTS performance_stage_job_idx
                ON performance_stage_attempts(job_id, id);
            """
        )


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _elapsed_ms(start: object, end: object) -> int | None:
    started = _parse_iso(start)
    completed = _parse_iso(end)
    if not started or not completed:
        return None
    return max(0, round((completed - started).total_seconds() * 1000))


def _safe_error(value: object) -> str | None:
    if value in {None, ""}:
        return None
    text = str(value)
    replacements = (
        ("authorization", "authorization=[REDACTED]"),
        ("private_key", "private_key=[REDACTED]"),
        ("credential", "credential=[REDACTED]"),
    )
    lowered = text.lower()
    if any(key in lowered for key, _ in replacements):
        return "Provider or worker error details were redacted"
    return text[-500:]


def record_stage_started(database_path: Path, job_id: str, stage: str) -> int:
    ensure_schema(database_path)
    with closing(_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(attempt_number), 0) AS attempt_number
            FROM performance_stage_attempts
            WHERE job_id = ? AND stage = ?
            """,
            (job_id, stage),
        ).fetchone()
        attempt = int(row["attempt_number"]) + 1
        connection.execute(
            """
            INSERT INTO performance_stage_attempts(
                job_id, stage, attempt_number, status, started_at
            ) VALUES (?, ?, ?, 'running', ?)
            """,
            (job_id, stage, attempt, _iso()),
        )
        connection.commit()
    return attempt


def _finish_latest_stage(
    database_path: Path,
    job_id: str,
    stage: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    ensure_schema(database_path)
    completed_at = _iso()
    with closing(_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT id, started_at
            FROM performance_stage_attempts
            WHERE job_id = ? AND stage = ? AND status = 'running'
            ORDER BY attempt_number DESC
            LIMIT 1
            """,
            (job_id, stage),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            """
            UPDATE performance_stage_attempts
            SET status = ?, completed_at = ?, active_duration_ms = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                completed_at,
                _elapsed_ms(row["started_at"], completed_at),
                _safe_error(error),
                row["id"],
            ),
        )
        connection.commit()


def record_stage_completed(database_path: Path, job_id: str, stage: str) -> None:
    _finish_latest_stage(database_path, job_id, stage, status="completed")


def record_stage_stopped(
    database_path: Path,
    job_id: str,
    stage: str | None,
    *,
    status: str,
    error: str | None = None,
) -> None:
    if stage:
        _finish_latest_stage(
            database_path,
            job_id,
            stage,
            status=status,
            error=error,
        )


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_UP))


def _token_value(usage: dict[str, Any], names: tuple[str, ...]) -> int:
    for name in names:
        value = usage.get(name)
        if value not in {None, ""}:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _stage_attempts(database_path: Path, job_id: str, end_at: datetime) -> list[dict[str, Any]]:
    ensure_schema(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT stage, attempt_number, status, started_at, completed_at,
                   active_duration_ms, error
            FROM performance_stage_attempts
            WHERE job_id = ?
            ORDER BY id
            """,
            (job_id,),
        ).fetchall()
        if not rows:
            rows = connection.execute(
                """
                SELECT stage, attempt_count AS attempt_number, status,
                       started_at, completed_at, NULL AS active_duration_ms, error
                FROM stage_runs
                WHERE job_id = ?
                ORDER BY started_at
                """,
                (job_id,),
            ).fetchall()
    attempts: list[dict[str, Any]] = []
    for row in rows:
        completed_at = row["completed_at"]
        duration = row["active_duration_ms"]
        if duration is None and row["started_at"]:
            duration = _elapsed_ms(row["started_at"], completed_at or end_at.isoformat())
        attempts.append(
            {
                "stage": row["stage"],
                "attemptNumber": int(row["attempt_number"] or 1),
                "status": row["status"],
                "startedAt": row["started_at"],
                "completedAt": completed_at,
                "activeDurationMs": int(duration or 0),
                "error": _safe_error(row["error"]),
            }
        )
    return attempts


def _pause_metrics(events: list[sqlite3.Row], end_at: datetime) -> tuple[int, list[dict[str, Any]]]:
    open_pause: tuple[datetime, str | None] | None = None
    intervals: list[dict[str, Any]] = []
    total_ms = 0
    for event in events:
        event_type = str(event["event_type"])
        at = _parse_iso(event["created_at"])
        if not at:
            continue
        if event_type == "job_paused" and open_pause is None:
            open_pause = (at, event["actor"])
        elif event_type in {"job_resumed", "job_cancelled"} and open_pause:
            duration = max(0, round((at - open_pause[0]).total_seconds() * 1000))
            intervals.append(
                {
                    "pausedAt": open_pause[0].isoformat(),
                    "resumedAt": at.isoformat(),
                    "durationMs": duration,
                    "actor": open_pause[1],
                }
            )
            total_ms += duration
            open_pause = None
    if open_pause:
        duration = max(0, round((end_at - open_pause[0]).total_seconds() * 1000))
        intervals.append(
            {
                "pausedAt": open_pause[0].isoformat(),
                "resumedAt": None,
                "durationMs": duration,
                "actor": open_pause[1],
            }
        )
        total_ms += duration
    return total_ms, intervals


def _chunk_metrics(job_dir: Path, config: CostConfig) -> list[dict[str, Any]]:
    plan_payload = _read_json(job_dir / "chunk-plan.json", {})
    plan = plan_payload.get("chunks") if isinstance(plan_payload, dict) else []
    if not isinstance(plan, list):
        plan = []
    result: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for item in plan:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["chunk_index"])
            start_ms = int(item["source_start_ms"])
            end_ms = int(item["source_end_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        manifest = _read_json(
            job_dir / "chunks" / f"chunk-{index:03d}" / "manifest.json", {}
        )
        manifest = manifest if isinstance(manifest, dict) else {}
        manifest_path = job_dir / "chunks" / f"chunk-{index:03d}" / "manifest.json"
        seen.add(manifest_path)
        duration_ms = max(0, end_ms - start_ms)
        cost = (
            Decimal(duration_ms)
            / Decimal("60000")
            * config.chirp_usd_per_minute
        )
        submitted_at = manifest.get("submitted_at")
        submitted_marker = bool(submitted_at or manifest.get("operation_name") or str(manifest.get("status") or "") in COMMITTED_CHUNK_STATES)
        provider_completed_at = manifest.get("provider_completed_at")
        recovered_at = manifest.get("recovered_at") or manifest.get("completed_at")
        if not recovered_at:
            partial = _read_json(
                job_dir / "chunks" / f"chunk-{index:03d}" / "partial-transcript.json",
                {},
            )
            if isinstance(partial, dict):
                recovered_at = partial.get("completedAt")
        result.append(
            {
                "chunkIndex": index,
                "startMs": start_ms,
                "endMs": end_ms,
                "audioDurationMs": duration_ms,
                "status": str(manifest.get("status") or "WAITING"),
                "attemptCount": int(manifest.get("attempt_count") or (1 if submitted_at else 0)),
                "submittedAt": submitted_at,
                "providerCompletedAt": provider_completed_at,
                "recoveredAt": recovered_at,
                "submitLatencyMs": int(manifest.get("submit_latency_ms") or 0),
                "providerProcessingMs": int(
                    manifest.get("provider_processing_ms")
                    or (_elapsed_ms(submitted_at, provider_completed_at) or 0)
                ),
                "recoveryDelayMs": int(
                    manifest.get("recovery_delay_ms")
                    or (_elapsed_ms(provider_completed_at, recovered_at) or 0)
                ),
                "totalWallMs": int(
                    manifest.get("total_wall_ms")
                    or (_elapsed_ms(submitted_at, recovered_at) or 0)
                ),
                "wordCount": int(manifest.get("word_count") or 0),
                "billedAudioSeconds": round(duration_ms / 1000, 3),
                "estimatedCostUsd": _money(cost),
                "errorCode": (
                    manifest.get("error", {}).get("code")
                    if isinstance(manifest.get("error"), dict)
                    else None
                ),
                "role": manifest.get("role", "base"),
                "isPatch": manifest.get("role") == "patch" or bool(manifest.get("patch_mode")),
                "operationName": manifest.get("operation_name"),
                "countedAsSubmitted": submitted_marker,
            }
        )
    # Patch/recovery chunks are intentionally not required to be in the
    # original plan. Include every durable manifest so local cost reports do
    # not undercount partial retries.
    for manifest_path in sorted((job_dir / "chunks").glob("chunk-*/manifest.json")):
        if manifest_path in seen:
            continue
        manifest = _read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            continue
        try:
            index = int(manifest_path.parent.name.split("-")[-1])
            start_ms = int(manifest.get("source_start_ms", 0))
            end_ms = int(manifest.get("source_end_ms", 0))
        except (TypeError, ValueError):
            continue
        submitted_at = manifest.get("submitted_at")
        submitted_marker = bool(submitted_at or manifest.get("operation_name") or str(manifest.get("status") or "") in COMMITTED_CHUNK_STATES)
        duration_ms = max(0, end_ms - start_ms)
        cost = Decimal(duration_ms) / Decimal("60000") * config.chirp_usd_per_minute
        result.append({
            "chunkIndex": index, "startMs": start_ms, "endMs": end_ms,
            "audioDurationMs": duration_ms, "status": str(manifest.get("status") or "WAITING"),
            "attemptCount": int(manifest.get("attempt_count") or (1 if submitted_at else 0)),
            "submittedAt": submitted_at, "providerCompletedAt": manifest.get("provider_completed_at"),
            "recoveredAt": manifest.get("recovered_at") or manifest.get("completed_at"),
            "submitLatencyMs": int(manifest.get("submit_latency_ms") or 0),
            "providerProcessingMs": int(manifest.get("provider_processing_ms") or 0),
            "recoveryDelayMs": int(manifest.get("recovery_delay_ms") or 0),
            "totalWallMs": int(manifest.get("total_wall_ms") or 0),
            "wordCount": int(manifest.get("word_count") or 0),
            "billedAudioSeconds": round(duration_ms / 1000, 3),
            "estimatedCostUsd": _money(cost),
            "errorCode": manifest.get("error", {}).get("code") if isinstance(manifest.get("error"), dict) else None,
            "role": manifest.get("role", "patch"), "isPatch": True,
            "operationName": manifest.get("operation_name"),
            "countedAsSubmitted": submitted_marker,
        })
    # A retry can be preserved under attempts/<id>/manifest.json while the
    # root manifest points at the latest attempt. Count each submitted provider
    # operation once. If old deployments only retained attempt_count, use that
    # as a clearly labelled conservative fallback rather than silently
    # undercounting it.
    archived_by_chunk: dict[int, list[Path]] = {}
    for attempt_path in sorted((job_dir / "chunks").glob("chunk-*/attempts/*/manifest.json")):
        try:
            index = int(attempt_path.parents[2].name.split("-")[-1])
        except (IndexError, ValueError):
            continue
        archived_by_chunk.setdefault(index, []).append(attempt_path)

    seen_operations = {
        str(item["operationName"])
        for item in result
        if item.get("operationName")
    }
    for item in result:
        archive_count = len(archived_by_chunk.get(int(item["chunkIndex"]), []))
        attempts = max(0, int(item.get("attemptCount") or 0))
        item["billedAttemptCount"] = 1 if item["countedAsSubmitted"] else 0
        if item["countedAsSubmitted"] and attempts > 1 and not archive_count:
            item["billedAttemptCount"] = attempts
            item["estimatedCostUsd"] = _money(
                Decimal(item["estimatedCostUsd"]) * Decimal(attempts)
            )
            item["costEvidence"] = "manifest_attempt_count_fallback"
        else:
            item["costEvidence"] = "submitted_manifest"

    for index, paths in archived_by_chunk.items():
        for attempt_path in paths:
            manifest = _read_json(attempt_path, {})
            if not isinstance(manifest, dict):
                continue
            submitted_at = manifest.get("submitted_at")
            operation_name = manifest.get("operation_name")
            submitted = bool(
                submitted_at
                or operation_name
                or str(manifest.get("status") or "") in COMMITTED_CHUNK_STATES
            )
            operation_key = str(operation_name or "")
            if operation_key and operation_key in seen_operations:
                continue
            if operation_key:
                seen_operations.add(operation_key)
            start_ms = int(manifest.get("source_start_ms", 0) or 0)
            end_ms = int(manifest.get("source_end_ms", 0) or 0)
            duration_ms = max(0, end_ms - start_ms)
            if not duration_ms:
                # Old manifests may lack coordinates; use the parent chunk's
                # plan interval as an estimate and mark the evidence.
                parent = next((item for item in result if int(item["chunkIndex"]) == index), None)
                duration_ms = int(parent["audioDurationMs"]) if parent else 0
            cost = Decimal(duration_ms) / Decimal("60000") * config.chirp_usd_per_minute
            result.append(
                {
                    "chunkIndex": index,
                    "attemptId": attempt_path.parent.name,
                    "startMs": start_ms,
                    "endMs": end_ms,
                    "audioDurationMs": duration_ms,
                    "status": str(manifest.get("status") or "UNKNOWN"),
                    "attemptCount": 1,
                    "billedAttemptCount": 1 if submitted else 0,
                    "submittedAt": submitted_at,
                    "providerCompletedAt": manifest.get("provider_completed_at"),
                    "recoveredAt": manifest.get("recovered_at") or manifest.get("completed_at"),
                    "submitLatencyMs": int(manifest.get("submit_latency_ms") or 0),
                    "providerProcessingMs": int(manifest.get("provider_processing_ms") or 0),
                    "recoveryDelayMs": int(manifest.get("recovery_delay_ms") or 0),
                    "totalWallMs": int(manifest.get("total_wall_ms") or 0),
                    "wordCount": int(manifest.get("word_count") or 0),
                    "billedAudioSeconds": round(duration_ms / 1000, 3),
                    "estimatedCostUsd": _money(cost),
                    "errorCode": manifest.get("error", {}).get("code") if isinstance(manifest.get("error"), dict) else None,
                    "role": manifest.get("role", "retry"),
                    "isPatch": manifest.get("role") == "patch" or bool(manifest.get("patch_mode")),
                    "operationName": operation_name,
                    "countedAsSubmitted": submitted,
                    "costEvidence": "archived_attempt_manifest",
                }
            )
    return result


def _gemini_calls(job_dir: Path, config: CostConfig) -> list[dict[str, Any]]:
    paths: list[tuple[str, Path]] = []
    glossary = job_dir / "glossary" / "global-terms.json"
    if glossary.is_file():
        paths.append(("glossary", glossary))
    for correction_dir in ("correction-v2", "correction-cascade-v1", "correction-m3-v1"):
        kind = "correction-m3" if correction_dir == "correction-m3-v1" else "correction"
        for path in sorted((job_dir / correction_dir).glob("*.json")):
            paths.append((kind, path))
    calls: list[dict[str, Any]] = []
    for kind, path in paths:
        payload = _read_json(path, {})
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage_metadata")
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = _token_value(
            usage,
            (
                "prompt_token_count",
                "input_token_count",
                "input_tokens",
                "promptTokenCount",
                "inputTokenCount",
            ),
        )
        output_tokens = _token_value(
            usage,
            (
                "candidates_token_count",
                "output_token_count",
                "output_tokens",
                "candidatesTokenCount",
                "outputTokenCount",
            ),
        )
        provider = "minimax" if payload.get("provider") == "minimax" or kind == "correction-m3" else "google-vertex-ai"
        cost = Decimal("0") if provider == "minimax" else (
            Decimal(input_tokens)
            * config.gemini_input_usd_per_million_tokens
            / Decimal("1000000")
            + Decimal(output_tokens)
            * config.gemini_output_usd_per_million_tokens
            / Decimal("1000000")
        )
        calls.append(
            {
                "callId": path.stem,
                "kind": kind,
                "model": payload.get("model") or "gemini-3.7-flash",
                "provider": provider,
                "sourceStartMs": payload.get("source_start_ms"),
                "sourceEndMs": payload.get("source_end_ms"),
                "requestStartedAt": payload.get("request_started_at"),
                "responseCompletedAt": payload.get("response_completed_at"),
                "latencyMs": int(
                    payload.get("latency_ms")
                    or _elapsed_ms(
                        payload.get("request_started_at"),
                        payload.get("response_completed_at"),
                    )
                    or 0
                ),
                "attemptCount": int(payload.get("attempt_count") or 1),
                "retryCount": max(0, int(payload.get("attempt_count") or 1) - 1),
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "estimatedCostUsd": _money(cost),
                "billingMode": "token_plan" if provider == "minimax" else "vertex_estimate",
                "cached": bool(payload.get("cache_hit")),
                "promptVersion": payload.get("prompt_version"),
            }
        )
    return calls


def estimated_accrued_cost(database_path: Path, data_dir: Path, job_id: str) -> Decimal:
    config = CostConfig.from_env()
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT processing_strategy FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    config = config.for_processing_strategy(
        (row["processing_strategy"] if row else None) or DEFAULT_PROCESSING_STRATEGY
    )
    job_dir = data_dir / "jobs" / job_id
    chunks = _chunk_metrics(job_dir, config) if job_dir.is_dir() else []
    chirp = sum(
        (
            Decimal(item["estimatedCostUsd"])
            for item in chunks
            if item["countedAsSubmitted"] or item["status"] in COMMITTED_CHUNK_STATES
        ),
        Decimal("0"),
    )
    gemini = sum(
        (Decimal(item["estimatedCostUsd"]) for item in _gemini_calls(job_dir, config)),
        Decimal("0"),
    )
    return (chirp + gemini).quantize(Decimal("0.0001"), rounding=ROUND_UP)


def build_performance_summary(database_path: Path, data_dir: Path, job_id: str) -> dict[str, Any]:
    ensure_schema(database_path)
    with _connection(database_path) as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise LookupError("Job not found")
        events = connection.execute(
            """
            SELECT event_type, actor, created_at
            FROM job_events WHERE job_id = ? ORDER BY id
            """,
            (job_id,),
        ).fetchall()
    now = _now()
    status = str(job["status"])
    end_at = _parse_iso(job["updated_at"]) if status in FINAL_JOB_STATES else now
    end_at = end_at or now
    attempts = _stage_attempts(database_path, job_id, end_at)
    paused_ms, pause_intervals = _pause_metrics(events, end_at)
    first_stage = min(
        (_parse_iso(item["startedAt"]) for item in attempts if item.get("startedAt")),
        default=None,
    )
    approved_at = _parse_iso(job["approved_at"])
    created_at = _parse_iso(job["created_at"]) or end_at
    elapsed_start = approved_at or created_at
    total_elapsed_ms = max(0, round((end_at - elapsed_start).total_seconds() * 1000))
    queue_ms = (
        max(0, round((first_stage - approved_at).total_seconds() * 1000))
        if first_stage and approved_at
        else 0
    )
    active_processing_ms = sum(int(item["activeDurationMs"]) for item in attempts)
    wall_processing_ms = max(0, total_elapsed_ms - queue_ms - paused_ms)
    job_dir = data_dir / "jobs" / job_id
    config = CostConfig.from_env().for_processing_strategy(
        job["processing_strategy"] or DEFAULT_PROCESSING_STRATEGY
    )
    chunks = _chunk_metrics(job_dir, config) if job_dir.is_dir() else []
    gemini_calls = _gemini_calls(job_dir, config) if job_dir.is_dir() else []
    audio_duration_ms = round(float(job["duration_seconds"] or 0) * 1000)
    accrued = estimated_accrued_cost(database_path, data_dir, job_id)
    rtf = (
        round(wall_processing_ms / audio_duration_ms, 4)
        if audio_duration_ms > 0
        else None
    )
    active_rtf = (
        round(active_processing_ms / audio_duration_ms, 4)
        if audio_duration_ms > 0
        else None
    )
    audio_hours = Decimal(audio_duration_ms) / Decimal("3600000") if audio_duration_ms else Decimal("0")
    cost_per_hour = accrued / audio_hours if audio_hours > 0 else Decimal("0")

    stage_totals: dict[str, int] = {}
    for item in attempts:
        stage_totals[item["stage"]] = stage_totals.get(item["stage"], 0) + int(item["activeDurationMs"])
    longest_stages = sorted(stage_totals.items(), key=lambda item: item[1], reverse=True)
    suggestions: list[str] = []
    if queue_ms > 60_000:
        suggestions.append("排隊時間偏長；檢查單來源檔全域 lease、Worker 是否忙碌或輪詢間隔是否過大。")
    recovery_delays = [int(item["recoveryDelayMs"]) for item in chunks if item["recoveryDelayMs"]]
    if recovery_delays and max(recovery_delays) > 60_000:
        suggestions.append("部分 Chirp 結果已完成但回收延遲超過 60 秒；可檢討 GCS 結果監看併發與輪詢策略。")
    retries = sum(max(0, int(item["attemptCount"]) - 1) for item in chunks)
    if retries:
        suggestions.append(f"Chirp 分段共發生 {retries} 次額外嘗試；應檢查配額、429、網路與特定音檔品質。")
    gemini_retries = sum(max(0, int(item["attemptCount"]) - 1) for item in gemini_calls)
    if gemini_retries:
        suggestions.append(f"Gemini 共發生 {gemini_retries} 次額外嘗試；提高併發前先確認 429 與延遲分布。")
    unpriced_retries = sum(int(item.get("retryCount", 0)) for item in gemini_calls)
    if unpriced_retries:
        suggestions.append("Gemini 重試的個別 token usage 未完整保存；目前金額為保守 application estimate，需以 Cloud Billing 對帳。")
    if rtf is not None and rtf > 1:
        suggestions.append("整體處理速度慢於音訊即時播放；優先檢查最長階段與 Canary 等待比例。")
    if not suggestions:
        suggestions.append("尚未偵測明顯瓶頸；累積至少 15、60、120 分鐘三組樣本後比較 P50/P95。")

    return {
        "jobId": job_id,
        "jobStatus": status,
        "audioDurationMs": audio_duration_ms,
        "totalElapsedMs": total_elapsed_ms,
        "queueMs": queue_ms,
        "pausedMs": paused_ms,
        "wallProcessingMs": wall_processing_ms,
        "activeStageDurationMs": active_processing_ms,
        "realTimeFactor": rtf,
        "activeRealTimeFactor": active_rtf,
        "estimatedAccruedCostUsd": _money(accrued),
        "estimatedCostPerAudioHourUsd": _money(cost_per_hour),
        "stageAttempts": attempts,
        "stageTotals": [
            {"stage": stage, "durationMs": duration}
            for stage, duration in longest_stages
        ],
        "pauseIntervals": pause_intervals,
        "chunks": chunks,
        "geminiCalls": gemini_calls,
        "accounting": {
            "chirpRetryCount": retries,
            "geminiRetryCount": gemini_retries,
            "unpricedGeminiRetryCount": unpriced_retries,
            "patchChunkCount": sum(bool(item.get("isPatch")) for item in chunks),
            "unfinishedSubmittedChunkCount": sum(item.get("countedAsSubmitted") and item["status"] not in {"SUCCEEDED", "EMPTY_SILENCE", "FAILED", "CANCELLED"} for item in chunks),
        },
        "bottleneckSuggestions": suggestions,
        "generatedAt": _iso(),
        "accountingNote": "Application estimates only; Cloud Billing is authoritative.",
    }


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def write_performance_reports(job_dir: Path, summary: dict[str, Any]) -> dict[str, Path]:
    json_path = job_dir / "performance-report.json"
    csv_path = job_dir / "performance-report.csv"
    html_path = job_dir / "performance-report.html"
    _atomic_text(json_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["category", "item", "status", "duration_ms", "input_units", "output_units", "estimated_cost_usd"])
    for item in summary.get("stageAttempts", []):
        writer.writerow(["stage", f"{item['stage']}#{item['attemptNumber']}", item["status"], item["activeDurationMs"], "", "", ""])
    for item in summary.get("chunks", []):
        writer.writerow(["chirp_chunk", item["chunkIndex"], item["status"], item["totalWallMs"], item["billedAudioSeconds"], item["wordCount"], item["estimatedCostUsd"]])
    for item in summary.get("geminiCalls", []):
        writer.writerow(["gemini_call", item["callId"], item["kind"], item["latencyMs"], item["inputTokens"], item["outputTokens"], item["estimatedCostUsd"]])
    _atomic_text(csv_path, output.getvalue())

    rows = "".join(
        f"<tr><td>{html.escape(str(item['stage']))}</td><td>{item['attemptNumber']}</td><td>{html.escape(str(item['status']))}</td><td>{item['activeDurationMs']}</td></tr>"
        for item in summary.get("stageAttempts", [])
    )
    suggestions = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in summary.get("bottleneckSuggestions", [])
    )
    document = f"""<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><title>Performance {html.escape(summary['jobId'])}</title><style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#172033}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd4e0;padding:10px;text-align:left}}.metric{{display:inline-block;margin:8px;padding:14px;border:1px solid #ccd4e0;border-radius:10px}}</style><h1>任務效能報告</h1><div class='metric'>音訊 {summary['audioDurationMs']} ms</div><div class='metric'>總經過 {summary['totalElapsedMs']} ms</div><div class='metric'>RTF {summary['realTimeFactor']}</div><div class='metric'>預估費用 US${summary['estimatedAccruedCostUsd']}</div><h2>階段嘗試</h2><table><thead><tr><th>階段</th><th>嘗試</th><th>狀態</th><th>有效時間 ms</th></tr></thead><tbody>{rows}</tbody></table><h2>瓶頸建議</h2><ul>{suggestions}</ul><p>{html.escape(summary['accountingNote'])}</p></html>"""
    _atomic_text(html_path, document)
    return {"json": json_path, "csv": csv_path, "html": html_path}
