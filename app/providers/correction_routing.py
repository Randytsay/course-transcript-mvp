"""Provider-agnostic correction routing decisions.

The live provider adapters are deliberately outside this module. These pure
functions define the safety contract for choosing MiniMax M3 or Gemini 3.7
Flash and for deciding whether one provider failure should permanently switch
the remainder of a job to Gemini.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.jobs.correction_policy import GEMINI_FIRST, M3_FIRST, normalize_correction_policy


class CorrectionProvider(StrEnum):
    GEMINI = "gemini-3.7-flash"
    MINIMAX_M3 = "minimax-m3"


class M3QuotaState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ProviderFailureKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    USAGE_LIMIT = "usage_limit"
    AUTHENTICATION = "authentication"
    TRANSIENT_EXHAUSTED = "transient_exhausted"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RouteDecision:
    requested_policy: str
    provider: CorrectionProvider
    fallback_provider: CorrectionProvider | None
    reason: str
    m3_feature_enabled: bool
    m3_quota_state: M3QuotaState


@dataclass(frozen=True)
class FailureDecision:
    retry_same_provider: bool
    switch_to_gemini_for_rest_of_job: bool
    fail_closed: bool
    reason: str


def choose_initial_route(
    *,
    requested_policy: str,
    m3_feature_enabled: bool,
    m3_quota_state: M3QuotaState,
) -> RouteDecision:
    policy = normalize_correction_policy(requested_policy)
    if policy == GEMINI_FIRST:
        return RouteDecision(
            requested_policy=policy,
            provider=CorrectionProvider.GEMINI,
            fallback_provider=None,
            reason="gemini_requested",
            m3_feature_enabled=m3_feature_enabled,
            m3_quota_state=m3_quota_state,
        )

    if policy != M3_FIRST:
        raise AssertionError(f"Unhandled correction policy: {policy}")
    if not m3_feature_enabled:
        return RouteDecision(
            requested_policy=policy,
            provider=CorrectionProvider.GEMINI,
            fallback_provider=None,
            reason="m3_feature_disabled",
            m3_feature_enabled=False,
            m3_quota_state=m3_quota_state,
        )
    if m3_quota_state is not M3QuotaState.AVAILABLE:
        return RouteDecision(
            requested_policy=policy,
            provider=CorrectionProvider.GEMINI,
            fallback_provider=None,
            reason=(
                "m3_quota_unavailable"
                if m3_quota_state is M3QuotaState.UNAVAILABLE
                else "m3_quota_unknown"
            ),
            m3_feature_enabled=True,
            m3_quota_state=m3_quota_state,
        )
    return RouteDecision(
        requested_policy=policy,
        provider=CorrectionProvider.MINIMAX_M3,
        fallback_provider=CorrectionProvider.GEMINI,
        reason="m3_available",
        m3_feature_enabled=True,
        m3_quota_state=m3_quota_state,
    )


def decide_provider_failure(
    provider: CorrectionProvider,
    failure_kind: ProviderFailureKind,
) -> FailureDecision:
    if provider is CorrectionProvider.GEMINI:
        return FailureDecision(
            retry_same_provider=False,
            switch_to_gemini_for_rest_of_job=False,
            fail_closed=True,
            reason="gemini_failure_requires_existing_raw_fallback_contract",
        )

    if failure_kind is ProviderFailureKind.RATE_LIMIT:
        return FailureDecision(
            retry_same_provider=True,
            switch_to_gemini_for_rest_of_job=False,
            fail_closed=False,
            reason="m3_rate_limit_retry_with_backoff",
        )
    if failure_kind in {
        ProviderFailureKind.USAGE_LIMIT,
        ProviderFailureKind.TRANSIENT_EXHAUSTED,
        ProviderFailureKind.INVALID_RESPONSE,
    }:
        return FailureDecision(
            retry_same_provider=False,
            switch_to_gemini_for_rest_of_job=True,
            fail_closed=False,
            reason=f"m3_{failure_kind.value}_switch_to_gemini",
        )
    if failure_kind is ProviderFailureKind.AUTHENTICATION:
        return FailureDecision(
            retry_same_provider=False,
            switch_to_gemini_for_rest_of_job=False,
            fail_closed=True,
            reason="m3_authentication_error_must_surface_configuration_problem",
        )
    return FailureDecision(
        retry_same_provider=False,
        switch_to_gemini_for_rest_of_job=True,
        fail_closed=False,
        reason="m3_unknown_failure_switch_to_gemini",
    )
