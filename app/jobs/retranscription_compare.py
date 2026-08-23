"""Provider-free comparison between accepted and retranscription candidate chunks.

The comparison is derived evidence only. It never mutates the accepted chunk and
never auto-applies a candidate. Operator review remains mandatory.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from app.providers.asr_quality import _chunk_metrics


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _quality_context(job_dir: Path, chunk_index: int) -> dict[str, Any]:
    report = _read_json(Path(job_dir) / "asr-quality.json", {})
    chunks = report.get("chunks", []) if isinstance(report, dict) else []
    if not isinstance(chunks, list):
        return {}
    for item in chunks:
        if isinstance(item, dict) and int(item.get("chunk_index", -1)) == chunk_index:
            return {
                "severity": item.get("severity"),
                "reasons": list(item.get("reasons") or []),
                "score": item.get("score"),
            }
    return {}


def build_candidate_comparison(
    *,
    job_dir: Path,
    candidate_relpath: str,
    chunk_index: int,
    source_chunk_sha256: str,
    current_source_chunk_sha256: str,
) -> dict[str, Any]:
    """Build a deterministic original-vs-candidate report."""
    job_dir = Path(job_dir)
    accepted_chunk = job_dir / "chunks" / f"chunk-{chunk_index:03d}"
    candidate_root = job_dir / candidate_relpath
    candidate_chunk = candidate_root / "chunks" / f"chunk-{chunk_index:03d}"
    original_partial = _read_json(accepted_chunk / "partial-transcript.json", {})
    candidate_partial = _read_json(candidate_chunk / "partial-transcript.json", {})
    if not isinstance(original_partial, dict) or not isinstance(candidate_partial, dict):
        raise RuntimeError("原始或候選 partial-transcript.json 無效")

    original_text = str(original_partial.get("rawText") or "")
    candidate_text = str(candidate_partial.get("rawText") or "")
    original_metrics = _chunk_metrics(accepted_chunk)["metrics"]
    candidate_metrics = _chunk_metrics(candidate_chunk)["metrics"]
    similarity = round(
        difflib.SequenceMatcher(None, original_text, candidate_text, autojunk=False).ratio(),
        4,
    )

    deltas = {
        "char_count": int(candidate_metrics["char_count"]) - int(original_metrics["char_count"]),
        "word_count": int(candidate_metrics["word_count"]) - int(original_metrics["word_count"]),
        "density_chars_per_min": round(
            float(candidate_metrics["density_chars_per_min"])
            - float(original_metrics["density_chars_per_min"]),
            2,
        ),
        "recognized_span_ratio": round(
            float(candidate_metrics["recognized_span_ratio"])
            - float(original_metrics["recognized_span_ratio"]),
            4,
        ),
        "longest_gap_ratio": round(
            float(candidate_metrics["longest_gap_ratio"])
            - float(original_metrics["longest_gap_ratio"]),
            4,
        ),
        "repeat_trigram_ratio": round(
            float(candidate_metrics["repeat_trigram_ratio"])
            - float(original_metrics["repeat_trigram_ratio"]),
            4,
        ),
    }
    signals: list[str] = []
    if deltas["recognized_span_ratio"] >= 0.05:
        signals.append("candidate_recognized_span_improved")
    if deltas["longest_gap_ratio"] <= -0.10:
        signals.append("candidate_longest_gap_reduced")
    if deltas["repeat_trigram_ratio"] >= 0.10:
        signals.append("candidate_repeat_pattern_worse")
    if similarity < 0.50:
        signals.append("candidate_text_changed_substantially")
    if original_text == candidate_text:
        signals.append("candidate_text_unchanged")

    return {
        "schema_version": "asr-retranscription-compare-v1",
        "chunk_index": chunk_index,
        "source_evidence": {
            "requested_chunk_sha256": source_chunk_sha256,
            "current_chunk_sha256": current_source_chunk_sha256,
            "unchanged": source_chunk_sha256 == current_source_chunk_sha256,
        },
        "original_quality_gate": _quality_context(job_dir, chunk_index),
        "original": {
            "raw_text": original_text,
            "word_count": original_partial.get("wordCount"),
            "metrics": original_metrics,
        },
        "candidate": {
            "raw_text": candidate_text,
            "word_count": candidate_partial.get("wordCount"),
            "metrics": candidate_metrics,
        },
        "comparison": {
            "text_similarity_ratio": similarity,
            "text_changed": original_text != candidate_text,
            "metric_deltas": deltas,
            "signals": signals,
        },
        "decision": "operator_review_required",
        "auto_apply": False,
        "paid_provider_calls_for_comparison": 0,
    }


def write_candidate_comparison(**kwargs: Any) -> dict[str, Any]:
    payload = build_candidate_comparison(**kwargs)
    path = Path(kwargs["job_dir"]) / str(kwargs["candidate_relpath"]) / "comparison.json"
    _atomic_json(path, payload)
    return payload
