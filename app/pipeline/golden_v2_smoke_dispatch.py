"""Trusted-client handoff: source+budget gate -> one provider call -> receipt.

No environment credential retrieval, flag-based approval, cloud permissions
or scheduler here. Production controller must supply *actual* owner/billing
verifiers and SDK client objects; importing this file is provider-free.
"""
from __future__ import annotations

from typing import Any, Callable

from app.pipeline.golden_v2_smoke_gcs import verify_staged_clip
from app.pipeline.golden_v2_smoke_ledger import SingleClipSmokeLedger
from app.providers.golden_v2_smoke_providers import (
    chirp_smoke_call, gemini_smoke_call, smoke_cost_cents,
)


def execute_trusted_smoke_stage(
    *, trial: SingleClipSmokeLedger, model: str, project: str, bucket: str,
    storage_client: Any, vertex_client: Any, speech_client: Any,
    verify_owner: Callable[[dict[str, Any]], bool],
    verify_capability_and_pricing: Callable[[dict[str, Any], str], bool],
    gemini_part_factory: Callable[..., Any] | None = None,
    gemini_config_factory: Callable[..., Any] | None = None,
    chirp_request_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read-only byte verification before budget CAS; send one paid request."""
    staged = verify_staged_clip(
        trial=trial, storage_client=storage_client, bucket=bucket)
    def invoke(_path, selected: str):
        sink = lambda event: trial.record_provider_event(model=selected, event=event)
        if selected == "chirp_3":
            return chirp_smoke_call(
                speech_client=speech_client, project=project,
                bucket=bucket, clip_sha256=staged["clip_sha256"],
                gcs_uri=staged["uri"],
                request_factory=chirp_request_factory,
                evidence_sink=sink,
            )
        return gemini_smoke_call(
            client=vertex_client, model=selected,
            bucket=bucket, clip_sha256=staged["clip_sha256"],
            gcs_uri=staged["uri"],
            part_factory=gemini_part_factory,
            config_factory=gemini_config_factory,
            evidence_sink=sink,
        )
    return trial.execute_one(
        model=model, provider_call=invoke,
        cost_accountant=smoke_cost_cents,
        verify_owner=verify_owner,
        verify_capability_and_pricing=verify_capability_and_pricing,
    )
