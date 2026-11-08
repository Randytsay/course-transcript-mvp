"""Offline, gold-reference-only CER benchmark for Golden V2 engines.

Never call a model's agreement rate "accuracy": a reviewed human reference is
required. Large classes must be split into ordered, manually aligned windows.
"""
from __future__ import annotations

import math
import unicodedata


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch.casefold() for ch in text if unicodedata.category(ch)[0] in {"L", "N"})


def _error_counts(gold: str, hypothesis: str) -> tuple[int, int, int]:
    """Return (substitution, deletion, insertion) minimal edit path."""
    previous = [(j, 0, 0, j) for j in range(len(hypothesis) + 1)]
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


def evaluate_paired_engine_benchmark(
    human_gold_windows: list[dict],
    engine_outputs: dict[str, dict],
    *,
    max_characters_per_window: int = 4000,
) -> dict:
    """Compare engines on identical source-bound windows and reviewed gold.

    Each gold row needs ``window_id``, source SHA, bounds, category, text,
    ``human_verified=True``, a gold artifact SHA and reviewer reference. Each
    engine row needs the same window ID/source SHA, transcript text and raw
    provider evidence SHA. Cost/latency are optional per-window observations;
    any missing value makes that engine's total unknown instead of zero.

    This validates supplied provenance fields, not the authenticity of the
    human review or the bytes behind an evidence SHA. It performs no IO.
    """
    if not isinstance(human_gold_windows, list) or not human_gold_windows:
        raise ValueError("need manually reviewed gold windows")
    if not isinstance(engine_outputs, dict) or not engine_outputs:
        raise ValueError("need at least one engine result")

    def valid_sha(value: object) -> bool:
        return (isinstance(value, str) and len(value) == 64
                and all(char in "0123456789abcdef" for char in value))

    gold_by_id: dict[str, dict] = {}
    for row in human_gold_windows:
        if not isinstance(row, dict):
            raise ValueError("gold window must be an object")
        window_id = row.get("window_id")
        source_sha = row.get("source_sha256")
        start_ms, end_ms = row.get("start_ms"), row.get("end_ms")
        category, text = row.get("category"), row.get("text")
        if (not isinstance(window_id, str) or not window_id.strip()
                or window_id in gold_by_id
                or not valid_sha(source_sha)
                or type(start_ms) is not int or type(end_ms) is not int
                or start_ms < 0 or end_ms <= start_ms
                or not isinstance(category, str) or not category.strip()
                or not isinstance(text, str) or not text.strip()
                or row.get("human_verified") is not True
                or not valid_sha(row.get("gold_evidence_sha256"))
                or not isinstance(row.get("reviewer_reference"), str)
                or not row["reviewer_reference"].strip()):
            raise ValueError("gold window lacks unique bounds or human-review provenance")
        gold_by_id[window_id] = row

    intervals_by_source: dict[str, list[tuple[int, int]]] = {}
    for row in gold_by_id.values():
        intervals_by_source.setdefault(row["source_sha256"], []).append(
            (row["start_ms"], row["end_ms"])
        )
    for intervals in intervals_by_source.values():
        intervals.sort()
        if any(current[0] < previous[1]
               for previous, current in zip(intervals, intervals[1:])):
            raise ValueError("paired gold windows must not overlap")

    if (type(max_characters_per_window) is not int
            or not 1 <= max_characters_per_window <= 100_000):
        raise ValueError("invalid per-window character limit")

    def optional_nonnegative_number(value: object) -> bool:
        if value is None:
            return True
        if type(value) not in {int, float} or value < 0:
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    engines: dict[str, dict] = {}
    for engine, result in engine_outputs.items():
        if (not isinstance(engine, str) or not engine.strip()
                or not isinstance(result, dict)
                or not isinstance(result.get("windows"), list)):
            raise ValueError("engine result needs a name and windows list")
        rows_by_id: dict[str, dict] = {}
        for row in result["windows"]:
            if not isinstance(row, dict):
                raise ValueError("engine window must be an object")
            window_id = row.get("window_id")
            if (not isinstance(window_id, str) or window_id not in gold_by_id
                    or window_id in rows_by_id):
                raise ValueError("engine window ID is unknown or duplicated")
            if row.get("source_sha256") != gold_by_id[window_id]["source_sha256"]:
                raise ValueError("engine window source differs from paired gold")
            if (not isinstance(row.get("text"), str)
                    or not valid_sha(row.get("raw_evidence_sha256"))):
                raise ValueError("engine window transcript/raw evidence SHA is invalid")
            if (not optional_nonnegative_number(row.get("cost_usd"))
                    or not optional_nonnegative_number(row.get("elapsed_seconds"))):
                raise ValueError("engine window cost or latency is invalid")
            rows_by_id[window_id] = row
        if set(rows_by_id) != set(gold_by_id):
            raise ValueError("every engine must cover the exact paired gold windows")

        ordered_ids = sorted(
            gold_by_id,
            key=lambda key: (gold_by_id[key]["source_sha256"],
                             gold_by_id[key]["start_ms"],
                             gold_by_id[key]["end_ms"], key),
        )
        engine_windows = [rows_by_id[window_id] for window_id in ordered_ids]
        gold_texts = [gold_by_id[window_id]["text"] for window_id in ordered_ids]
        candidate_texts = [row["text"] for row in engine_windows]
        aggregate = evaluate_character_errors(
            gold_texts, candidate_texts,
            max_characters_per_window=max_characters_per_window,
        )
        categories: dict[str, dict] = {}
        for category in sorted({gold_by_id[key]["category"] for key in ordered_ids}):
            category_ids = [key for key in ordered_ids
                            if gold_by_id[key]["category"] == category]
            categories[category] = evaluate_character_errors(
                [gold_by_id[key]["text"] for key in category_ids],
                [rows_by_id[key]["text"] for key in category_ids],
                max_characters_per_window=max_characters_per_window,
            )

        costs = [row.get("cost_usd") for row in engine_windows]
        elapsed = [row.get("elapsed_seconds") for row in engine_windows]
        engines[engine] = {
            "window_count": len(engine_windows),
            "character_metrics": aggregate,
            "by_category": categories,
            "cost_usd": round(sum(costs), 8) if all(value is not None for value in costs) else None,
            "cost_complete": all(value is not None for value in costs),
            "elapsed_seconds_sum": round(sum(elapsed), 6) if all(value is not None for value in elapsed) else None,
            "elapsed_complete": all(value is not None for value in elapsed),
            "provider_evidence_by_window": {
                window_id: rows_by_id[window_id]["raw_evidence_sha256"]
                for window_id in ordered_ids
            },
        }

    return {
        "schema": "golden-v2-paired-benchmark-v1",
        "pairing": "EXACT_SOURCE_SHA_AND_WINDOW_ID",
        "human_review_provenance_required": True,
        "window_count": len(gold_by_id),
        "paired_window_manifest": [
            {
                "window_id": window_id,
                "source_sha256": gold_by_id[window_id]["source_sha256"],
                "start_ms": gold_by_id[window_id]["start_ms"],
                "end_ms": gold_by_id[window_id]["end_ms"],
                "category": gold_by_id[window_id]["category"],
                "gold_evidence_sha256": gold_by_id[window_id]["gold_evidence_sha256"],
                "reviewer_reference": gold_by_id[window_id]["reviewer_reference"],
            }
            for window_id in sorted(
                gold_by_id,
                key=lambda key: (gold_by_id[key]["source_sha256"],
                                 gold_by_id[key]["start_ms"],
                                 gold_by_id[key]["end_ms"], key),
            )
        ],
        "engines": engines,
        "accuracy_claimed": False,
        "model_agreement_used_as_gold": False,
    }
