"""Offline, gold-reference-only CER benchmark for Golden V2 engines.

Never call a model's agreement rate "accuracy": a reviewed human reference is
required. Large classes must be split into ordered, manually aligned windows.
"""
from __future__ import annotations

import unicodedata


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch.casefold() for ch in text if unicodedata.category(ch)[0] in {"L", "N"})


def _error_counts(gold: str, hypothesis: str) -> tuple[int, int, int]:
    """Return (substitution, deletion, insertion) minimal edit path."""
    previous = [(0, 0, 0, j) for j in range(len(hypothesis) + 1)]
    for i, expected in enumerate(gold, 1):
        current = [(i, 0, i, 0)]
        for j, seen in enumerate(hypothesis, 1):
            if expected == seen:
                current.append(previous[j-1])
            else:
                choices = (
                    (previous[j-1][0] + 1, previous[j-1][1] + 1, previous[j-1][2], previous[j-1][3]),
                    (previous[j][0] + 1, previous[j][1], previous[j][2] + 1, previous[j][3]),
                    (current[-1][0] + 1, current[-1][1], current[-1][2], current[-1][3] + 1),
                )
                current.append(min(choices))
        previous = current
    _, substitutions, deletions, insertions = previous[-1]
    return substitutions, deletions, insertions


def evaluate_character_errors(
    human_verified_gold_windows: list[str],
    candidate_windows: list[str],
    *,
    max_characters_per_window: int = 4000,
) -> dict:
    """Windows must correspond to the same manually reviewed audio intervals.

    Running this with another model's output as gold is forbidden by contract.
    """
    if not human_verified_gold_windows or len(human_verified_gold_windows) != len(candidate_windows):
        raise ValueError("need matched human-gold and candidate windows")
    total = 0
    substitutions = deletions = insertions = 0
    for gold_raw, candidate_raw in zip(human_verified_gold_windows, candidate_windows):
        gold, candidate = _normalize(gold_raw), _normalize(candidate_raw)
        if not gold or max(len(gold), len(candidate)) > max_characters_per_window:
            raise ValueError("missing gold or oversized/unsegmented evaluation window")
        s, d, i = _error_counts(gold, candidate)
        total += len(gold)
        substitutions += s
        deletions += d
        insertions += i
    return {
        "metric": "CER_HUMAN_GOLD",
        "gold_verified_required": True,
        "reference_characters": total,
        "window_count": len(human_verified_gold_windows),
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "character_error_rate": (substitutions + deletions + insertions) / total,
        "character_accuracy_not_inferred_from_model_agreement": True,
    }
