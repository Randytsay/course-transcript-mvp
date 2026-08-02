"""Auditable estimated-cost calculations.

These values are estimates for application-side safeguards. Cloud Billing is
the authoritative source for actual charges and promotional-credit handling.
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_UP


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_UP)


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
        return cls(
            project_limit_usd=Decimal(
                os.environ.get("COURSE_TRANSCRIPT_COST_LIMIT_USD", "200")
            ),
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
