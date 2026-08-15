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


def build_performance_summary(
    database_path: Path,
    data_dir: Path,
    job_id: str,
) -> dict[str, Any]:
    summary = base.build_performance_summary(database_path, data_dir, job_id)
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
    if ranked:
        stage, _, share = ranked[0]
        label = {
            "correction": "Gemini 校正",
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
    calls = summary.get("geminiCalls")
    chirp_cost = sum(
        (_decimal(item.get("estimatedCostUsd")) for item in chunks if isinstance(item, dict)),
        Decimal("0"),
    ) if isinstance(chunks, list) else Decimal("0")
    gemini_cost = sum(
        (_decimal(item.get("estimatedCostUsd")) for item in calls if isinstance(item, dict)),
        Decimal("0"),
    ) if isinstance(calls, list) else Decimal("0")
    config = base.CostConfig.from_env()
    summary["providerCostBreakdown"] = {
        "chirpEstimatedUsd": str(chirp_cost.quantize(Decimal("0.0001"))),
        "geminiEstimatedUsd": str(gemini_cost.quantize(Decimal("0.0001"))),
        "chirpEstimatedTwd": str(config.usd_as_twd(chirp_cost)),
        "geminiEstimatedTwd": str(config.usd_as_twd(gemini_cost)),
        "highestCostProvider": "chirp" if chirp_cost >= gemini_cost else "gemini",
    }
    if chirp_cost or gemini_cost:
        suggestions.append(
            f"預估費用較高的是{'Chirp' if chirp_cost >= gemini_cost else 'Gemini'}："
            f"Chirp {config.usd_as_twd(chirp_cost)} 元，Gemini {config.usd_as_twd(gemini_cost)} 元。"
        )
    if isinstance(calls, list) and len(calls) > 200:
        suggestions.append(
            f"Gemini 共 {len(calls)} 次呼叫；建議使用 60 秒校正視窗並保留失敗時自動拆分。"
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
