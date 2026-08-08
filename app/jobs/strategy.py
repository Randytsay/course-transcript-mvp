"""Processing-strategy choices shared by the API, cost ledger, and workers."""
from __future__ import annotations

from typing import Final

DYNAMIC_BATCHING: Final = "DYNAMIC_BATCHING"
STANDARD_BATCH: Final = "STANDARD_BATCH"
DEFAULT_PROCESSING_STRATEGY: Final = DYNAMIC_BATCHING
PROCESSING_STRATEGIES: Final = frozenset({DYNAMIC_BATCHING, STANDARD_BATCH})


def normalize_processing_strategy(value: object) -> str:
    """Return a supported application strategy or raise a clear error."""
    strategy = str(value or DEFAULT_PROCESSING_STRATEGY).strip().upper()
    aliases = {
        "DYNAMIC": DYNAMIC_BATCHING,
        "ECONOMICAL": DYNAMIC_BATCHING,
        "BATCH": DYNAMIC_BATCHING,
        "STANDARD": STANDARD_BATCH,
        "FAST": STANDARD_BATCH,
        "PROCESSING_STRATEGY_UNSPECIFIED": STANDARD_BATCH,
    }
    strategy = aliases.get(strategy, strategy)
    if strategy not in PROCESSING_STRATEGIES:
        raise ValueError(
            "processing_strategy must be DYNAMIC_BATCHING or STANDARD_BATCH"
        )
    return strategy


def is_dynamic_batching(value: object) -> bool:
    return normalize_processing_strategy(value) == DYNAMIC_BATCHING


def provider_processing_strategy(value: object) -> str:
    """Map the application choice to Speech-to-Text's request value."""
    return (
        "DYNAMIC_BATCHING"
        if is_dynamic_batching(value)
        else "PROCESSING_STRATEGY_UNSPECIFIED"
    )


def strategy_label(value: object) -> str:
    return "經濟模式（Dynamic Batch）" if is_dynamic_batching(value) else "快速模式（Standard Batch）"
