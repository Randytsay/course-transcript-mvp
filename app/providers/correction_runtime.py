"""Runtime orchestration for the opt-in MiniMax-first correction path."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.jobs.correction_policy import GEMINI_FIRST, M3_FIRST, normalize_correction_policy
from app.providers.correction_routing import (
    CorrectionProvider,
    M3QuotaState,
    ProviderFailureKind,
    choose_initial_route,
    decide_provider_failure,
)
from app.providers.minimax_provider import MiniMaxCorrectionClient, MiniMaxProviderError
from app.providers.minimax_quota import MiniMaxQuotaClient, MiniMaxQuotaSnapshot
from app.providers.correction_evidence import summarize_routing


def _true(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_runtime_ref(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or len(text) > 200 or any(ord(char) < 32 for char in text):
        return None
    return text


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class CorrectionRuntime:
    """One source-job router; it never re-enters M3 after switching away."""

    def __init__(
        self,
        *,
        requested_policy: str,
        m3_feature_enabled: bool,
        quota_check_enabled: bool,
        quota_client: MiniMaxQuotaClient,
        m3_client: MiniMaxCorrectionClient,
        gemini_corrector: Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, dict[str, Any]]],
        manifest_path: Path,
        context: str = "",
    ) -> None:
        self.requested_policy = normalize_correction_policy(requested_policy)
        self.m3_feature_enabled = m3_feature_enabled
        self.quota_check_enabled = quota_check_enabled
        self.quota_client = quota_client
        self.m3_client = m3_client
        self.gemini_corrector = gemini_corrector
        self.manifest_path = manifest_path
        self.context = context
        # Capture provenance once at source-job runtime initialization so later
        # report regeneration cannot misattribute historical jobs to a newer
        # container revision or a changed provider output-token limit.
        self.runtime_git_sha = _safe_runtime_ref(
            os.getenv("COURSE_TRANSCRIPT_RUNTIME_GIT_SHA")
            or os.getenv("COURSE_TRANSCRIPT_RELEASE_TAG")
        )
        self.docker_image_revision = _safe_runtime_ref(
            os.getenv("COURSE_TRANSCRIPT_DOCKER_IMAGE_REVISION")
            or os.getenv("COURSE_TRANSCRIPT_RELEASE_TAG")
        )
        raw_output_limit = getattr(self.m3_client, "max_output_tokens", None)
        try:
            parsed_output_limit = int(raw_output_limit) if raw_output_limit is not None else None
        except (TypeError, ValueError):
            parsed_output_limit = None
        self.m3_max_output_tokens = (
            parsed_output_limit if parsed_output_limit is not None and parsed_output_limit > 0 else None
        )
        self.m3_reasoning_split = bool(getattr(self.m3_client, "reasoning_split", False))
        # Routing state and manifest writes are serialized. Provider calls are
        # intentionally *not* all serialized: M3 remains single-flight so one
        # failure can atomically switch the rest of the job to Gemini, while
        # Gemini calls run outside this lock and retain the outer correction
        # pool's configured parallelism.
        self._lock = threading.RLock()
        self._switches: list[dict[str, Any]] = []
        self._gemini_inflight = 0
        self._effective_gemini_concurrency = 0
        self._m3_inflight = 0
        self._effective_m3_concurrency = 0
        self._counts = {
            CorrectionProvider.MINIMAX_M3.value: 0,
            CorrectionProvider.GEMINI.value: 0,
            "chirp-3-raw": 0,
        }
        self.quota = self._initial_quota()
        self.decision = choose_initial_route(
            requested_policy=self.requested_policy,
            m3_feature_enabled=self.m3_feature_enabled,
            m3_quota_state=self.quota.state,
        )
        self.active_provider = self.decision.provider
        self._write_manifest()

    def _initial_quota(self) -> MiniMaxQuotaSnapshot:
        if self.requested_policy != M3_FIRST:
            return MiniMaxQuotaSnapshot(M3QuotaState.UNKNOWN, "", reason="gemini_requested")
        if not self.m3_feature_enabled:
            return MiniMaxQuotaSnapshot(M3QuotaState.UNKNOWN, "", reason="m3_feature_disabled")
        if not self.quota_check_enabled:
            return MiniMaxQuotaSnapshot(M3QuotaState.UNKNOWN, "", reason="quota_check_disabled")
        # A source job always starts with a forced refresh.  The client itself
        # may retain only a short cache for read/status endpoints.
        return self.quota_client.get_quota(force_refresh=True)

    def _write_manifest(self) -> None:
        _atomic_json(
            self.manifest_path,
            {
                "requested_policy": self.requested_policy,
                "initial_provider": self.decision.provider.value,
                "initial_route_reason": self.decision.reason,
                "m3_quota_state_at_start": self.quota.state.value,
                "m3_quota_checked_at": self.quota.checked_at or None,
                "m3_quota_source_pool": self.quota.source_pool,
                "m3_interval_remaining": self.quota.interval_remaining,
                "m3_weekly_remaining": self.quota.weekly_remaining,
                "m3_interval_reset_at": self.quota.interval_reset_at,
                "m3_weekly_reset_at": self.quota.weekly_reset_at,
                "m3_quota_reason": self.quota.reason,
                "provider_switches": list(self._switches),
                "segment_counts": dict(self._counts),
                "effective_gemini_concurrency": self._effective_gemini_concurrency,
                "effective_m3_concurrency": self._effective_m3_concurrency,
                "runtime_git_sha": self.runtime_git_sha,
                "docker_image_revision": self.docker_image_revision,
                "m3_max_output_tokens": self.m3_max_output_tokens,
                "m3_reasoning_split": self.m3_reasoning_split,
                "chirp_raw_immutable": True,
                "timestamps_immutable": True,
            },
        )

    def _count_result(
        self,
        result: dict[str, dict[str, Any]],
        default_provider: CorrectionProvider,
    ) -> None:
        for entry in result.values():
            provider = (
                "chirp-3-raw"
                if bool(entry.get("fallback_to_raw"))
                else default_provider.value
            )
            self._counts[provider] = self._counts.get(provider, 0) + 1

    def _record_result(
        self,
        result: dict[str, dict[str, Any]],
        provider: CorrectionProvider,
    ) -> None:
        """Update shared accounting without serializing the provider request."""
        with self._lock:
            self._count_result(result, provider)
            self._write_manifest()

    def _switch_to_gemini(self, *, kind: ProviderFailureKind, segment_id: str) -> None:
        self.quota_client.invalidate()
        if self.active_provider is CorrectionProvider.GEMINI:
            return
        self._switches.append(
            {
                "from": self.active_provider.value,
                "to": CorrectionProvider.GEMINI.value,
                "reason": kind.value,
                "at_segment_id": segment_id,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        self.active_provider = CorrectionProvider.GEMINI
        self._write_manifest()

    def correct_window(
        self,
        items: list[dict[str, Any]],
        terms: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        # M3 stays single-flight under the routing lock. This guarantees that a
        # failed M3 request can perform the one-way switch before another window
        # re-enters M3. Once Gemini is active we leave the lock before making the
        # provider call, so ThreadPoolExecutor parallelism is preserved.
        with self._lock:
            if self.active_provider is CorrectionProvider.MINIMAX_M3:
                self._m3_inflight += 1
                self._effective_m3_concurrency = max(
                    self._effective_m3_concurrency,
                    self._m3_inflight,
                )
                try:
                    result = self.m3_client.correct_window(
                        items,
                        terms,
                        context=self.context,
                    )
                    self._count_result(result, CorrectionProvider.MINIMAX_M3)
                    self._write_manifest()
                    return result
                except MiniMaxProviderError as exc:
                    failure = decide_provider_failure(
                        CorrectionProvider.MINIMAX_M3,
                        exc.kind,
                    )
                    if failure.fail_closed:
                        self._write_manifest()
                        raise
                    # The adapter has already completed its bounded retry
                    # budget for rate/transient/invalid responses.  At this
                    # point the runtime applies the handoff's one-way switch
                    # for the remainder of this source job.
                    self._switch_to_gemini(
                        kind=exc.kind,
                        segment_id=str(items[0]["segment_id"]),
                    )
                finally:
                    self._m3_inflight -= 1
                    self._write_manifest()

        with self._lock:
            self._gemini_inflight += 1
            self._effective_gemini_concurrency = max(
                self._effective_gemini_concurrency,
                self._gemini_inflight,
            )
            self._write_manifest()
        try:
            result = self.gemini_corrector(items, terms)
            self._record_result(result, CorrectionProvider.GEMINI)
            return result
        finally:
            with self._lock:
                self._gemini_inflight -= 1
                self._write_manifest()


def _m3_terms_generator(client: MiniMaxCorrectionClient, *, context: str):
    def generate(raw_segments):
        from app.providers import correct_text as base
        source=[{"segment_id":x["segment_id"],"text":x["raw_text"]} for x in raw_segments]
        sha=hashlib.sha256(json.dumps(source,ensure_ascii=False,separators=(",",":")).encode()).hexdigest(); out=base.JOB/"glossary"; out.mkdir(parents=True,exist_ok=True); cache=out/"global-terms.json"
        try: cached=json.loads(cache.read_text()) if cache.exists() else None
        except (OSError,json.JSONDecodeError): cached=None
        if isinstance(cached,dict) and cached.get("provider")=="minimax" and cached.get("source_sha256")==sha: return cached.get("terms",[]) if isinstance(cached.get("terms",[]),list) else []
        try:
            record=client.extract_terms(raw_segments,context=context); terms=record.get("terms",[]) if isinstance(record.get("terms",[]),list) else []; record={**record,"source_sha256":sha,"cache_hit":False,"error_type":None,"safe_error":None}
        except Exception as exc:
            terms=[]; record={"provider":"minimax","model":os.getenv("MINIMAX_M3_MODEL","MiniMax-M3"),"prompt_version":"terminology-v1-minimax-m3","source_sha256":sha,"usage_metadata":{"request_count":0,"billing_mode":"token_plan"},"terms":[],"raw_response":None,"cache_hit":False,"error_type":type(exc).__name__,"safe_error":str(exc)[-500:]}
        _atomic_json(cache,record)
        with (out/"global-terms.csv").open("w",encoding="utf-8",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=["canonical","variants","confidence"]); writer.writeheader()
            for term in terms:
                if isinstance(term,dict): writer.writerow({"canonical":term.get("canonical",""),"variants":" | ".join(str(v) for v in term.get("variants",[])),"confidence":term.get("confidence","")})
        return terms
    return generate

def main() -> int:
    from app.providers import correct_text as base
    from app.providers import correct_text_legacy_hardened as legacy

    requested_policy = os.getenv("CORRECTION_REQUESTED_POLICY", GEMINI_FIRST)
    m3_enabled = _true("MINIMAX_M3_ENABLED")
    quota_enabled = _true("MINIMAX_M3_QUOTA_CHECK_ENABLED")
    m3_client = MiniMaxCorrectionClient(
        audit_dir=base.JOB / "correction-m3-v1",
    )
    quota_client = MiniMaxQuotaClient()
    runtime: CorrectionRuntime | None = None

    def gemini_corrector(items: list[dict[str, Any]], terms: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return legacy.correct_window(items, terms)

    runtime = CorrectionRuntime(
        requested_policy=requested_policy,
        m3_feature_enabled=m3_enabled,
        quota_check_enabled=quota_enabled,
        quota_client=quota_client,
        m3_client=m3_client,
        gemini_corrector=gemini_corrector,
        manifest_path=base.JOB / "correction-routing.json",
        context=base.correction_context_instruction(),
    )
    base.generate_json = legacy.generate_json
    original_terms = base.generate_terms
    original_correct_window = base.correct_window
    original_prompt = base.PROMPT_VERSION
    try:
        if runtime.decision.provider is CorrectionProvider.MINIMAX_M3:
            base.generate_terms = _m3_terms_generator(m3_client, context=runtime.context)
        base.correct_window = runtime.correct_window
        base.PROMPT_VERSION = "fixed-segments-v1-routed-correction"
        result = base.main()
    finally:
        base.generate_terms = original_terms
        base.correct_window = original_correct_window
        base.PROMPT_VERSION = original_prompt
    if result == 0:
        output_path = base.JOB / "subtitles-corrected.json"
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return 1
        provider_route = str(summarize_routing(base.JOB)["correction_route"])
        payload.update(
            {
                "source": "chirp_3_merged + routed text-only correction",
                "correction_provider": provider_route,
                "routing_manifest": "correction-routing.json",
                "chirp_raw_immutable": True,
                "timestamps_immutable": True,
            }
        )
        _atomic_json(output_path, payload)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
