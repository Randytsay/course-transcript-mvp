"""Auditable estimated-cost calculations.

These values are estimates for application-side safeguards. Cloud Billing is
the authoritative source for actual charges and promotional-credit handling.
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from .strategy import DYNAMIC_BATCHING, normalize_processing_strategy


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_UP)


def _twd_money(value: Decimal) -> Decimal:
    """Format an application estimate as TWD without changing USD accounting."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_UP)


def _env_true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CostConfig:
    project_limit_usd: Decimal = Decimal("200")
    warning_thresholds_usd: tuple[Decimal, ...] = (
        Decimal("50"),
        Decimal("100"),
        Decimal("160"),
        Decimal("190"),
    )
    chirp_usd_per_minute: Decimal = Decimal("0.016")
    gemini_input_usd_per_million_tokens: Decimal = Decimal("1.50")
    gemini_output_usd_per_million_tokens: Decimal = Decimal("7.50")
    estimated_input_tokens_per_audio_minute: int = 3500
    estimated_output_tokens_per_audio_minute: int = 1800
    chirp_retry_and_overlap_multiplier: Decimal = Decimal("1.10")
    contingency_multiplier: Decimal = Decimal("1.25")
    gcs_job_buffer_usd: Decimal = Decimal("0.05")
    pricing_version: str = "google-cloud-public-pricing-2026-07-31"
    usd_to_twd: Decimal = Decimal("32")
    budget_remaining_twd: Decimal | None = None
    budget_baseline_committed_usd: Decimal = Decimal("0")

    @classmethod
    def from_env(cls) -> "CostConfig":
        thresholds = tuple(
            Decimal(item.strip())
            for item in os.environ.get(
                "COURSE_TRANSCRIPT_COST_WARNING_THRESHOLDS_USD",
                "50,100,160,190",
            ).split(",")
            if item.strip()
        )
        dynamic_batching = _env_true("CHIRP_DYNAMIC_BATCHING", default=False)
        default_chirp_price = "0.003" if dynamic_batching else "0.016"
        default_pricing_version = (
            "google-cloud-speech-v2-dynamic-batching-2026-08"
            if dynamic_batching
            else "google-cloud-public-pricing-2026-07-31"
        )
        usd_to_twd = Decimal(
            os.environ.get("COURSE_TRANSCRIPT_USD_TO_TWD", "32")
        )
        if usd_to_twd <= 0:
            raise ValueError("COURSE_TRANSCRIPT_USD_TO_TWD must be positive")
        budget_remaining_raw = os.environ.get("COURSE_TRANSCRIPT_BUDGET_REMAINING_TWD")
        budget_remaining_twd = (
            Decimal(budget_remaining_raw) if budget_remaining_raw else None
        )
        if budget_remaining_twd is not None and budget_remaining_twd < 0:
            raise ValueError("COURSE_TRANSCRIPT_BUDGET_REMAINING_TWD cannot be negative")
        budget_baseline_committed_usd = Decimal(
            os.environ.get("COURSE_TRANSCRIPT_BUDGET_BASELINE_COMMITTED_USD", "0")
        )
        if budget_baseline_committed_usd < 0:
            raise ValueError(
                "COURSE_TRANSCRIPT_BUDGET_BASELINE_COMMITTED_USD cannot be negative"
            )
        project_limit_usd = Decimal(
            os.environ.get("COURSE_TRANSCRIPT_COST_LIMIT_USD", "200")
        )
        if budget_remaining_twd is not None:
            project_limit_usd = _money(
                budget_baseline_committed_usd + budget_remaining_twd / usd_to_twd
            )
        return cls(
            project_limit_usd=project_limit_usd,
            warning_thresholds_usd=thresholds,
            chirp_usd_per_minute=Decimal(
                os.environ.get(
                    "COURSE_TRANSCRIPT_CHIRP_USD_PER_MINUTE",
                    default_chirp_price,
                )
            ),
            gemini_input_usd_per_million_tokens=Decimal(
                os.environ.get("COURSE_TRANSCRIPT_GEMINI_INPUT_USD_PER_MILLION", "1.50")
            ),
            gemini_output_usd_per_million_tokens=Decimal(
                os.environ.get("COURSE_TRANSCRIPT_GEMINI_OUTPUT_USD_PER_MILLION", "7.50")
            ),
            estimated_input_tokens_per_audio_minute=int(
                os.environ.get("COURSE_TRANSCRIPT_ESTIMATED_INPUT_TOKENS_PER_MINUTE", "3500")
            ),
            estimated_output_tokens_per_audio_minute=int(
                os.environ.get("COURSE_TRANSCRIPT_ESTIMATED_OUTPUT_TOKENS_PER_MINUTE", "1800")
            ),
            chirp_retry_and_overlap_multiplier=Decimal(
                os.environ.get("COURSE_TRANSCRIPT_CHIRP_COST_MULTIPLIER", "1.10")
            ),
            contingency_multiplier=Decimal(
                os.environ.get("COURSE_TRANSCRIPT_COST_CONTINGENCY_MULTIPLIER", "1.25")
            ),
            gcs_job_buffer_usd=Decimal(
                os.environ.get("COURSE_TRANSCRIPT_GCS_JOB_BUFFER_USD", "0.05")
            ),
            pricing_version=os.environ.get(
                "COURSE_TRANSCRIPT_PRICING_VERSION",
                default_pricing_version,
            ),
            usd_to_twd=usd_to_twd,
            budget_remaining_twd=budget_remaining_twd,
            budget_baseline_committed_usd=budget_baseline_committed_usd,
        )

    def usd_as_twd(self, amount_usd: Decimal | str | int | float) -> Decimal:
        return _twd_money(Decimal(str(amount_usd)) * self.usd_to_twd)

    def budget_summary(self, committed_estimated_cost_usd: Decimal) -> dict[str, str]:
        """Return the user-facing TWD budget while preserving USD source data.

        A production budget can start from a new operator-defined balance while
        keeping historical USD reservations as a baseline. New reservations are
        therefore the only amounts deducted from that balance.
        """
        committed = Decimal(str(committed_estimated_cost_usd))
        if self.budget_remaining_twd is None:
            starting_exact = self.project_limit_usd * self.usd_to_twd
            spent_exact = committed * self.usd_to_twd
        else:
            starting_exact = self.budget_remaining_twd
            spent_usd = max(
                Decimal("0"), committed - self.budget_baseline_committed_usd
            )
            spent_exact = spent_usd * self.usd_to_twd
        starting = _twd_money(starting_exact)
        spent = _twd_money(spent_exact)
        remaining = max(Decimal("0"), starting_exact - spent_exact).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        return {
            "budget_currency": "TWD",
            "budget_starting_balance_twd": str(starting),
            "committed_estimated_cost_twd": str(spent),
            "remaining_estimated_budget_twd": str(remaining),
            "usd_to_twd": str(self.usd_to_twd),
        }

    def for_processing_strategy(self, strategy: object) -> "CostConfig":
        """Use the rate and pricing label for one immutable job choice.

        An explicit operator override remains authoritative; otherwise the
        two supported provider modes use their configured public estimates.
        """
        normalized = normalize_processing_strategy(strategy)
        if "COURSE_TRANSCRIPT_CHIRP_USD_PER_MINUTE" in os.environ:
            return self
        if normalized == DYNAMIC_BATCHING:
            return replace(
                self,
                chirp_usd_per_minute=Decimal("0.003"),
                pricing_version="google-cloud-speech-v2-dynamic-batching-2026-08",
            )
        return replace(
            self,
            chirp_usd_per_minute=Decimal("0.016"),
            pricing_version="google-cloud-public-pricing-2026-07-31",
        )


@dataclass(frozen=True)
class CostEstimate:
    duration_seconds: float
    chirp_billable_minutes: Decimal
    estimated_gemini_input_tokens: int
    estimated_gemini_output_tokens: int
    chirp_usd: Decimal
    gemini_input_usd: Decimal
    gemini_output_usd: Decimal
    gcs_buffer_usd: Decimal
    subtotal_usd: Decimal
    contingency_multiplier: Decimal
    estimated_total_usd: Decimal
    pricing_version: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key, value in tuple(payload.items()):
            if isinstance(value, Decimal):
                payload[key] = str(value)
        return payload


def estimate_job_cost(duration_seconds: float, config: CostConfig) -> CostEstimate:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be a positive finite number")

    audio_minutes = Decimal(str(duration_seconds)) / Decimal("60")
    billable_minutes = (
        audio_minutes * config.chirp_retry_and_overlap_multiplier
    ).quantize(Decimal("0.01"), rounding=ROUND_UP)
    whole_audio_minutes = math.ceil(duration_seconds / 60)
    input_tokens = whole_audio_minutes * config.estimated_input_tokens_per_audio_minute
    output_tokens = whole_audio_minutes * config.estimated_output_tokens_per_audio_minute

    chirp_usd = _money(billable_minutes * config.chirp_usd_per_minute)
    gemini_input_usd = _money(
        Decimal(input_tokens)
        * config.gemini_input_usd_per_million_tokens
        / Decimal("1000000")
    )
    gemini_output_usd = _money(
        Decimal(output_tokens)
        * config.gemini_output_usd_per_million_tokens
        / Decimal("1000000")
    )
    subtotal = _money(
        chirp_usd + gemini_input_usd + gemini_output_usd + config.gcs_job_buffer_usd
    )
    total = _money(subtotal * config.contingency_multiplier)
    return CostEstimate(
        duration_seconds=duration_seconds,
        chirp_billable_minutes=billable_minutes,
        estimated_gemini_input_tokens=input_tokens,
        estimated_gemini_output_tokens=output_tokens,
        chirp_usd=chirp_usd,
        gemini_input_usd=gemini_input_usd,
        gemini_output_usd=gemini_output_usd,
        gcs_buffer_usd=config.gcs_job_buffer_usd,
        subtotal_usd=subtotal,
        contingency_multiplier=config.contingency_multiplier,
        estimated_total_usd=total,
        pricing_version=config.pricing_version,
    )
