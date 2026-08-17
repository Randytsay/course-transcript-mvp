"""Enhanced single-job bottleneck guidance layered on existing evidence."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from app.jobs import performance as base


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _repair_stale_attempts(summary: dict[str, Any]) -> None:
    """Exclude legacy unclosed attempts when a newer same-stage attempt exists.

    Older workers could restart after recording a stage start without recording
    its stop. The base report then extends that `running` attempt to the job end
    and can make an unrelated stage look like the bottleneck. New workers close
    these rows explicitly; this repair keeps already-persisted historical rows
    auditable while excluding their unknowable duration from effective metrics.
    """
    attempts = summary.get("stageAttempts")
    attempts = attempts if isinstance(attempts, list) else []
    stale_count = 0
    for index, item in enumerate(attempts):
        if not isinstance(item, dict) or str(item.get("status")) != "running":
            continue
        stage = str(item.get("stage") or "")
        attempt_number = int(item.get("attemptNumber") or 0)
        superseded = any(
            isinstance(later, dict)
            and str(later.get("stage") or "") == stage
            and int(later.get("attemptNumber") or 0) > attempt_number
            for later in attempts[index + 1 :]
        )
        if not superseded:
            continue
        item["observedActiveDurationMs"] = int(item.get("activeDurationMs") or 0)
        item["activeDurationMs"] = 0
        item["reportingStatus"] = "superseded_unclosed"
        item["excludedFromEffectiveDuration"] = True
        stale_count += 1

    stage_totals: dict[str, int] = {}
    active_ms = 0
    for item in attempts:
        if not isinstance(item, dict):
            continue
        duration = max(0, int(item.get("activeDurationMs") or 0))
        active_ms += duration
        stage = str(item.get("stage") or "unknown")
        stage_totals[stage] = stage_totals.get(stage, 0) + duration

    summary["activeStageDurationMs"] = active_ms
    audio_duration_ms = int(summary.get("audioDurationMs") or 0)
    summary["activeRealTimeFactor"] = (
        round(active_ms / audio_duration_ms, 4) if audio_duration_ms > 0 else None
    )
    summary["stageTotals"] = [
        {"stage": stage, "durationMs": duration}
        for stage, duration in sorted(
            stage_totals.items(), key=lambda entry: entry[1], reverse=True
        )
    ]
    summary["staleStageAttemptCount"] = stale_count


def _provider_breakdown(calls: list[dict[str, Any]], provider: str) -> dict[str, int]:
    selected = [item for item in calls if str(item.get("provider") or "") == provider]
    return {
        "callCount": len(selected),
        "retryCount": sum(max(0, int(item.get("retryCount") or 0)) for item in selected),
        "inputTokens": sum(max(0, int(item.get("inputTokens") or 0)) for item in selected),
        "outputTokens": sum(max(0, int(item.get("outputTokens") or 0)) for item in selected),
        "latencyMs": sum(max(0, int(item.get("latencyMs") or 0)) for item in selected),
    }


def build_performance_summary(
    database_path: Path,
    data_dir: Path,
    job_id: str,
) -> dict[str, Any]:
    summary = base.build_performance_summary(database_path, data_dir, job_id)
    _repair_stale_attempts(summary)

    totals = summary.get("stageTotals")
    totals = totals if isinstance(totals, list) else []
    active_ms = max(1, int(summary.get("activeStageDurationMs") or 0))
    ranked: list[tuple[str, int, float]] = []
    for item in totals:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or "unknown")
        duration = int(item.get("durationMs") or 0)
        ranked.append((stage, duration, duration * 100 / active_ms))
        item["sharePercent"] = round(duration * 100 / active_ms, 1)
    ranked.sort(key=lambda value: value[1], reverse=True)

    suggestions: list[str] = []
    stale_count = int(summary.get("staleStageAttemptCount") or 0)
    if stale_count:
        suggestions.append(
            f"已排除 {stale_count} 筆被較新同階段嘗試取代、但舊版未關閉的 running attempt；"
            "其原始觀測值仍保留在 stageAttempts.observedActiveDurationMs。"
        )
    if ranked:
        stage, _, share = ranked[0]
        label = {
            "correction": "AI 文字校正",
            "chirp": "Chirp 辨識／等待",
            "drive_publish": "Drive 回寫",
            "normalize": "音訊正規化",
            "download": "來源下載",
        }.get(stage, stage)
        if share >= 70:
            suggestions.append(f"明顯瓶頸：{label}占有效處理時間 {share:.1f}%。")
        elif share >= 50:
            suggestions.append(f"主要瓶頸：{label}占有效處理時間 {share:.1f}%。")
        else:
            suggestions.append(f"目前最耗時階段為{label}，占有效處理時間 {share:.1f}%。")

    chunks = summary.get("chunks")
    raw_calls = summary.get("geminiCalls")
    chunks = chunks if isinstance(chunks, list) else []
    calls = [item for item in raw_calls if isinstance(item, dict)] if isinstance(raw_calls, list) else []
    # Preserve the legacy `geminiCalls` field for API compatibility, but expose
    # the accurate provider-neutral name for new consumers.
    summary["providerCalls"] = calls

    chirp_cost = sum(
        (_decimal(item.get("estimatedCostUsd")) for item in chunks if isinstance(item, dict)),
        Decimal("0"),
    )
    google_calls = [item for item in calls if item.get("provider") == "google-vertex-ai"]
    minimax_calls = [item for item in calls if item.get("provider") == "minimax"]
    gemini_cost = sum(
        (_decimal(item.get("estimatedCostUsd")) for item in google_calls),
        Decimal("0"),
    )
    minimax_cost = sum(
        (_decimal(item.get("estimatedCostUsd")) for item in minimax_calls),
        Decimal("0"),
    )
    config = base.CostConfig.from_env()
    costs = {
        "chirp": chirp_cost,
        "gemini": gemini_cost,
        "minimax": minimax_cost,
    }
    highest_cost_provider = max(costs, key=costs.get) if any(costs.values()) else None
    summary["providerCostBreakdown"] = {
        "chirpEstimatedUsd": str(chirp_cost.quantize(Decimal("0.0001"))),
        "geminiEstimatedUsd": str(gemini_cost.quantize(Decimal("0.0001"))),
        "minimaxEstimatedUsd": str(minimax_cost.quantize(Decimal("0.0001"))),
        "chirpEstimatedTwd": str(config.usd_as_twd(chirp_cost)),
        "geminiEstimatedTwd": str(config.usd_as_twd(gemini_cost)),
        "minimaxEstimatedTwd": str(config.usd_as_twd(minimax_cost)),
        "minimaxBillingMode": "token_plan" if minimax_calls else None,
        "highestCostProvider": highest_cost_provider,
    }
    summary["providerCallBreakdown"] = {
        "googleVertexAi": _provider_breakdown(calls, "google-vertex-ai"),
        "minimax": _provider_breakdown(calls, "minimax"),
    }

    accounting = summary.get("accounting")
    accounting = accounting if isinstance(accounting, dict) else {}
    google_retry_count = summary["providerCallBreakdown"]["googleVertexAi"]["retryCount"]
    minimax_retry_count = summary["providerCallBreakdown"]["minimax"]["retryCount"]
    accounting["geminiRetryCount"] = google_retry_count
    accounting["minimaxRetryCount"] = minimax_retry_count
    accounting["unpricedGeminiRetryCount"] = google_retry_count
    summary["accounting"] = accounting

    routing = base._read_json(
        data_dir / "jobs" / job_id / "correction-routing.json",
        {},
    )
    if isinstance(routing, dict) and routing:
        summary["correctionRouting"] = {
            "requestedPolicy": routing.get("requested_policy"),
            "initialProvider": routing.get("initial_provider"),
            "initialRouteReason": routing.get("initial_route_reason"),
            "providerSwitches": routing.get("provider_switches", []),
            "segmentCounts": routing.get("segment_counts", {}),
            "m3QuotaStateAtStart": routing.get("m3_quota_state_at_start"),
        }

    if chirp_cost or gemini_cost:
        suggestions.append(
            f"預估費用較高的是{'Chirp' if chirp_cost >= gemini_cost else 'Gemini'}："
            f"Chirp {config.usd_as_twd(chirp_cost)} 元，Gemini {config.usd_as_twd(gemini_cost)} 元。"
        )
    if minimax_calls:
        suggestions.append(
            f"MiniMax M3 共 {len(minimax_calls)} 筆 provider evidence、{minimax_retry_count} 次額外嘗試；"
            "Token Plan 以訂閱額度計，不併入本報表的邊際 API 金額。"
        )
    if len(google_calls) > 200:
        suggestions.append(
            f"Gemini 共 {len(google_calls)} 次呼叫；建議檢查實際平行度與 60 秒校正視窗拆分比例。"
        )
    if not suggestions:
        suggestions.append("樣本尚不足；請累積更多任務後比較 P50/P95。")
    summary["bottleneckSuggestions"] = suggestions
    return summary


write_performance_reports = base.write_performance_reports
ensure_schema = base.ensure_schema
record_stage_completed = base.record_stage_completed
record_stage_started = base.record_stage_started
record_stage_stopped = base.record_stage_stopped
