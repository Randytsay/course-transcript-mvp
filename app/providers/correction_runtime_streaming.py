"""Streaming-aware MiniMax correction runtime.

This module reuses the existing one-way routing semantics and only swaps the M3
client for the strict Streaming 2.0 transport when explicitly enabled.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.jobs.correction_policy import GEMINI_FIRST
from app.providers.correction_evidence import summarize_routing
from app.providers.correction_routing import CorrectionProvider
from app.providers.correction_runtime import (
    CorrectionRuntime,
    _atomic_json,
    _m3_terms_generator,
    _true,
)
from app.providers.minimax_quota import MiniMaxQuotaClient
from app.providers.minimax_streaming_provider import MiniMaxStreamingCorrectionClient


class StreamingCorrectionRuntime(CorrectionRuntime):
    """Persist transport provenance without changing routing decisions."""

    def _write_manifest(self) -> None:
        super()._write_manifest()
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        payload["m3_streaming_enabled"] = bool(
            getattr(self.m3_client, "streaming_enabled", False)
        )
        payload["m3_stream_deadline_seconds"] = getattr(
            self.m3_client,
            "stream_deadline_seconds",
            None,
        )
        payload["m3_transport"] = (
            "streaming_v2"
            if payload["m3_streaming_enabled"]
            else "non_stream"
        )
        _atomic_json(self.manifest_path, payload)


def main() -> int:
    from app.providers import correct_text as base
    from app.providers import correct_text_legacy_hardened as legacy

    requested_policy = os.getenv("CORRECTION_REQUESTED_POLICY", GEMINI_FIRST)
    m3_enabled = _true("MINIMAX_M3_ENABLED")
    quota_enabled = _true("MINIMAX_M3_QUOTA_CHECK_ENABLED")
    m3_client = MiniMaxStreamingCorrectionClient(
        audit_dir=base.JOB / "correction-m3-v1",
    )
    quota_client = MiniMaxQuotaClient()

    def gemini_corrector(items, terms):
        return legacy.correct_window(items, terms)

    runtime = StreamingCorrectionRuntime(
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
