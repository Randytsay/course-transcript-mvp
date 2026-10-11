"""Golden V2 provider-free durable stage bridge: compare → review/QA.

This is *not* the paid recognition runner and never touches Drive. Stage claims
come from the isolated SQLite ledger. Artifacts are immutable by content and
become resumable evidence once the stage transaction commits.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.gemini_primary_audio import _atomic_json
from app.providers.golden_v2_compare import compare_gemini_chirp
from app.providers.golden_v2_final_gate import validate_golden_v2


def _write_evidence(path: Path, data: dict[str, Any]) -> str:
    wire = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(wire).hexdigest()
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        prior = json.dumps(existing, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(prior).hexdigest() != digest:
            raise V2Conflict("immutable V2 stage artifact content mismatch")
        return digest
    _atomic_json(path, data)
    return digest


def complete_comparison(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    job_dir: Path, primary_text: str,
    chirp_words: list[dict[str, Any]],
) -> dict[str, Any]:
    if claim["stage"] != "compare":
        raise V2Conflict("comparison must own a comparison-stage claim")
    report = compare_gemini_chirp(primary_text, chirp_words)
    report["job_id"] = claim["job_id"]
    report["source_sha256"] = claim["source_sha"]
    digest = _write_evidence(job_dir / "golden-v2" / "engine-comparison.json", report)
    ledger.complete(
        claim, evidence_sha=digest, actual_cents=0,
        comparison_has_dispute=bool(report["pending_review_count"]),
    )
    return report


def complete_qa_or_hold(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    job_dir: Path, srt: str, transcript: str,
    chirp_word_boundaries_ms: set[int], acoustic_gate: str,
    semantic_gate: str, comparison: dict[str, Any],
    adjudications: list[dict[str, Any]],
) -> dict[str, Any]:
    if claim["stage"] != "golden_qa":
        raise V2Conflict("QA must own a golden_qa-stage claim")
    result = validate_golden_v2(
        srt=srt, transcript=transcript,
        chirp_word_boundaries_ms=chirp_word_boundaries_ms,
        acoustic_gate=acoustic_gate, semantic_gate=semantic_gate,
        comparison=comparison, adjudications=adjudications,
    )
    result["job_id"] = claim["job_id"]
    result["source_sha256"] = claim["source_sha"]
    digest = _write_evidence(job_dir / "golden-v2" / "golden-qa.json", result)
    if result["safe_publish_allowed"]:
        ledger.complete(claim, evidence_sha=digest, actual_cents=0)
    else:
        ledger.hold_for_review(claim, evidence_sha=digest)
    return result
