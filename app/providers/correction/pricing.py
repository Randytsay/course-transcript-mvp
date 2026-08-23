"""Pricing catalog — provider/model/mode aware and date-aware.

Sources:
- Vertex Gemini 3.7 Flash: official Google pricing, global, through
  2026-12-31 (Standard $0.75/$3.75 per 1M; Flex/Batch $0.375/$1.875),
  2027-01-01 onward Standard $1.50/$7.50, Flex/Batch $0.75/$3.75.
- OpenRouter: only from provider models-API pricing metadata; batch price
  unknown unless documented -> UNKNOWN-SAFELY (never fabricated, never $0).
- MiniMax: token plan / manual metadata only.

Unknown pricing is represented as None and displayed as「價格未知」— never 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class PricePoint:
    input_per_1m_usd: float | None
    output_per_1m_usd: float | None
    source: str
    effective_from: str
    effective_until: str | None = None


# Vertex Gemini 3.7 Flash — official published global pricing (USD / 1M tokens).
_VERTEX_G37_FLASH_GLOBAL = [
    # promotional-through-2026 window
    PricePoint(0.75, 3.75, "google-official-global", "2026-01-01", "2026-12-31"),
    PricePoint(0.375, 1.875, "google-official-global-flex", "2026-01-01", "2026-12-31"),
    # 2027 standard pricing
    PricePoint(1.50, 7.50, "google-official-global", "2027-01-01", None),
    PricePoint(0.75, 3.75, "google-official-global-flex", "2027-01-01", None),
]

CHIRP_DYNAMIC_BATCH_USD_PER_MINUTE = 0.003
CHIRP_STANDARD_USD_PER_MINUTE = 0.016


def vertex_price(*, mode: str, on: date,
                 location: str = "global") -> dict[str, Any]:
    """Date-aware Vertex pricing for gemini-3.7-flash.

    mode: REALTIME or BATCH (BATCH uses flex pricing).
    Non-global locations are not covered by the global snapshot -> unknown
    rather than wrong.
    """
    if location != "global":
        return {"input": None, "output": None, "known": False,
                "reason": f"region {location} 未有可靠價格資料"}
    flex = mode == "BATCH"
    for point in _VERTEX_G37_FLASH_GLOBAL:
        is_flex = "flex" in point.source
        if is_flex != flex:
            continue
        start = date.fromisoformat(point.effective_from)
        end = (date.fromisoformat(point.effective_until)
               if point.effective_until else date.max)
        if start <= on <= end:
            return {"input": point.input_per_1m_usd, "output": point.output_per_1m_usd,
                    "known": True, "source": point.source}
    return {"input": None, "output": None, "known": False,
            "reason": "此日期無對應價格區間"}


def estimate_correction_cost(
    *, provider: str, model: str, mode: str,
    input_tokens: int, output_tokens: int, on: date,
    openrouter_pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate AI correction cost. Unknown pricing -> known=False, cost=None."""
    if provider == "vertex":
        p = vertex_price(mode=mode, on=on)
        if not p["known"]:
            return {**p, "estimated_cost_usd": None}
        cost = (input_tokens / 1e6 * p["input"]) + (output_tokens / 1e6 * p["output"])
        return {**p, "estimated_cost_usd": round(cost, 6)}
    if provider == "openrouter":
        pr = openrouter_pricing or {}
        pin, pout = pr.get("input"), pr.get("output")
        if pin is None or pout is None:
            return {"known": False, "estimated_cost_usd": None,
                    "reason": "OpenRouter 無法可靠取得此 model 價格"}
        cost = (input_tokens / 1e6 * float(pin)) + (output_tokens / 1e6 * float(pout))
        return {"known": True, "source": "openrouter-models-api",
                "estimated_cost_usd": round(cost, 6)}
    if provider == "minimax":
        return {"known": False, "estimated_cost_usd": None,
                "reason": "MiniMax 訂閱／Token Plan，不硬算 pay-as-you-go"}
    return {"known": False, "estimated_cost_usd": None, "reason": "未知 provider"}


def chirp_cost_usd(minutes: float, *, dynamic_batching: bool) -> float:
    per_min = (CHIRP_DYNAMIC_BATCH_USD_PER_MINUTE if dynamic_batching
               else CHIRP_STANDARD_USD_PER_MINUTE)
    return round(minutes * per_min, 6)
