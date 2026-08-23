"""Production compatibility bridge from M3_FIRST to the windowed M3 router.

The public production API persists the long-standing correction policy rather
than a provider profile. Dynamic Chirp work may finish hours after job
creation, so MiniMax Token Plan availability must be evaluated when the
correction stage actually starts, not when the job is created.

This module keeps that contract while dispatching an available M3_FIRST job to
the shared windowed correction router. Gemini remains the fail-closed path for
GEMINI_FIRST, disabled M3, disabled quota checking, unknown quota, unavailable
quota, or a quota-check failure.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.jobs.correction_policy import GEMINI_FIRST, M3_FIRST, normalize_correction_policy
from app.jobs.store import JobStore
from app.providers.correction.base import ProviderError
from app.providers.correction.registry import LEGACY_MINIMAX_PROFILE_ID
from app.providers.correction_routing import (
    CorrectionProvider,
    M3QuotaState,
    choose_initial_route,
)
from app.providers.minimax_quota import MiniMaxQuotaClient, MiniMaxQuotaSnapshot


def _true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _job_context() -> tuple[dict[str, Any], Path]:
    from app.providers.correction_runtime_bridge import context_for_job

    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    job_id = os.environ.get("JOB_NAME", "").strip()
    if not job_id:
        raise RuntimeError("M3 policy bridge missing JOB_NAME")
    record = JobStore(data_dir / "course-transcript.db").get_job(job_id)
    return context_for_job(record, data_dir), data_dir / "jobs" / job_id


def _quota_snapshot(policy: str) -> MiniMaxQuotaSnapshot:
    if policy != M3_FIRST:
        return MiniMaxQuotaSnapshot(
            M3QuotaState.UNKNOWN,
            "",
            reason="gemini_requested",
        )
    if not _true("MINIMAX_M3_ENABLED"):
        return MiniMaxQuotaSnapshot(
            M3QuotaState.UNKNOWN,
            "",
            reason="m3_feature_disabled",
        )
    if not _true("MINIMAX_M3_QUOTA_CHECK_ENABLED"):
        return MiniMaxQuotaSnapshot(
            M3QuotaState.UNKNOWN,
            "",
            reason="quota_check_disabled",
        )
    try:
        return MiniMaxQuotaClient().get_quota(force_refresh=True)
    except Exception:
        # No provider payload or exception text is persisted here. Ambiguous
        # quota state must remain fail-closed to Gemini.
        return MiniMaxQuotaSnapshot(
            M3QuotaState.UNKNOWN,
            "",
            reason="quota_check_failed",
        )


def _write_route_evidence(
    job_dir: Path,
    *,
    policy: str,
    quota: MiniMaxQuotaSnapshot,
    provider: CorrectionProvider,
    reason: str,
) -> None:
    from app.providers.correction_runtime_bridge import _atomic_json

    _atomic_json(
        job_dir / "correction-routing.json",
        {
            "requested_policy": policy,
            "initial_provider": provider.value,
            "initial_route_reason": reason,
            "m3_feature_enabled": _true("MINIMAX_M3_ENABLED"),
            "m3_quota_state_at_start": quota.state.value,
            "m3_quota_checked_at": quota.checked_at or None,
            "m3_quota_source_pool": quota.source_pool,
            "m3_interval_remaining": quota.interval_remaining,
            "m3_weekly_remaining": quota.weekly_remaining,
            "m3_interval_reset_at": quota.interval_reset_at,
            "m3_weekly_reset_at": quota.weekly_reset_at,
            "m3_quota_reason": quota.reason,
            "provider_switches": [],
            "segment_counts": {},
            "router": "windowed-provider-router-v1",
            "chirp_raw_immutable": True,
            "timestamps_immutable": True,
        },
    )


def _finalize_route_evidence(
    job_dir: Path,
    *,
    provider: CorrectionProvider,
    window_results: list[dict[str, Any]] | None = None,
    circuit_opened: bool = False,
) -> None:
    """Keep historical performance/UI routing accounting truthful."""
    from app.providers.correction_runtime_bridge import _atomic_json, _read_json

    route_path = job_dir / "correction-routing.json"
    route = _read_json(route_path, {})
    route = route if isinstance(route, dict) else {}
    corrected = _read_json(job_dir / "subtitles-corrected.json", {})
    segments = corrected.get("segments", []) if isinstance(corrected, dict) else []
    segments = segments if isinstance(segments, list) else []
    raw_count = sum(
        1
        for item in segments
        if isinstance(item, dict)
        and bool(item.get("correction_fallback") or item.get("fallback_to_raw"))
    )
    provider_count = max(0, len(segments) - raw_count)
    counts = {
        CorrectionProvider.MINIMAX_M3.value: 0,
        CorrectionProvider.GEMINI.value: 0,
        "chirp-3-raw": raw_count,
    }
    counts[provider.value] = provider_count
    route.update(
        {
            "segment_counts": counts,
            "window_results": list(window_results or []),
            "provider_circuit_opened": bool(circuit_opened),
        }
    )
    _atomic_json(route_path, route)


def main() -> int:
    """Run the current policy using a correction-time MiniMax route decision."""
    from app.providers import correct_text_legacy_hardened as legacy
    from app.providers.correction_runtime_bridge import (
        _write_corrected_outputs,
        run_module,
    )

    policy = normalize_correction_policy(
        os.environ.get("CORRECTION_REQUESTED_POLICY", GEMINI_FIRST)
    )
    quota = _quota_snapshot(policy)
    decision = choose_initial_route(
        requested_policy=policy,
        m3_feature_enabled=_true("MINIMAX_M3_ENABLED"),
        m3_quota_state=quota.state,
    )

    ctx, job_dir = _job_context()
    _write_route_evidence(
        job_dir,
        policy=policy,
        quota=quota,
        provider=decision.provider,
        reason=decision.reason,
    )

    if decision.provider is not CorrectionProvider.MINIMAX_M3:
        # Preserve the established Gemini implementation and its artifact/audit
        # contract whenever M3 is not explicitly safe to use at correction time.
        rc = legacy.main()
        if rc == 0:
            _finalize_route_evidence(job_dir, provider=CorrectionProvider.GEMINI)
        return rc

    ctx.update(
        {
            "correction_provider": "minimax",
            "correction_provider_profile_id": LEGACY_MINIMAX_PROFILE_ID,
            "correction_model": os.environ.get("MINIMAX_M3_MODEL", "MiniMax-M3"),
            "correction_execution_mode": "REALTIME",
            "correction_fallback_policy": "RAW_CHIRP_FALLBACK",
        }
    )
    try:
        result = run_module(ctx=ctx)
        status = str(result.get("correction_status") or "")
        _write_corrected_outputs(
            ctx,
            result,
            raw_response=result.get("correction_raw_response"),
            audit_status=status,
        )
        _finalize_route_evidence(
            job_dir,
            provider=CorrectionProvider.MINIMAX_M3,
            window_results=list(result.get("correction_window_results") or []),
            circuit_opened=bool(result.get("correction_provider_circuit_opened", False)),
        )
        print(
            "CORRECTION=PASS provider=minimax "
            f"status={status} policy={policy}",
            flush=True,
        )
        return 0
    except ProviderError as exc:
        # Authentication/quota configuration errors are intentionally visible
        # instead of being disguised as successful raw-Chirp correction.
        print(
            f"CORRECTION=FAIL ProviderError kind={exc.kind}: {exc.safe_message}",
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
