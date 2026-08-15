"""Feature-flagged compatibility entrypoint for hardened text correction.

The legacy hardened implementation remains the default production path and
retains its public testing/audit contract. The cost-aware cascade is opt-in
until real-provider A/B validation is complete.
"""
from __future__ import annotations

import os
from typing import Any

from app.providers import correct_text_cascade as cascade
from app.providers import correct_text_legacy_hardened as legacy

# Preserve the established public contract used by production-hardening tests
# and by any operator tooling that patches the provider module.
base = legacy.base
PROMPT_VERSION = legacy.PROMPT_VERSION
SAFE_PROMPT_VERSION = legacy.SAFE_PROMPT_VERSION
PRIMARY_MODEL = cascade.PRIMARY_MODEL
ESCALATION_MODEL = cascade.ESCALATION_MODEL

generate_json = legacy.generate_json
content_guard = legacy.content_guard


def correction_cascade_enabled() -> bool:
    return os.getenv("CORRECTION_CASCADE_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def correct_window(
    items: list[dict[str, Any]],
    terms: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Run the legacy hardened window while preserving patch compatibility."""
    original_generate = legacy.generate_json
    legacy.generate_json = generate_json
    try:
        return legacy.correct_window(items, terms)
    finally:
        legacy.generate_json = original_generate


def main() -> int:
    if os.getenv("MINIMAX_M3_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        from app.providers import correction_runtime

        return correction_runtime.main()
    if correction_cascade_enabled():
        return cascade.main()
    # Keep the default path byte-for-byte compatible in behavior with the
    # previously deployed hardened implementation.
    legacy.generate_json = generate_json
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
