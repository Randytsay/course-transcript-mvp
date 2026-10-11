"""Import *existing*, verified Chirp 3 artifacts into a V2 stage (no API calls).

An original V1 Chirp result can be independently compared after V2 Gemini
main text is complete, without re-billing provider recognition. The
source's normalized FLAC SHA and existing acoustic gate must match.
This never repairs or silently transforms Chirp words/timestamps.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_audio_staging import duration_seconds, file_sha256
from app.providers.gemini_primary_audio import _atomic_json


class ChirpReuseError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ChirpReuseError(f"required Chirp evidence missing: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChirpReuseError(f"invalid Chirp evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise ChirpReuseError("Chirp evidence must be JSON object")
    return value


def adopt_verified_chirp(
    *, ledger: GoldenV2Ledger, claim: dict[str, Any],
    historical_job_dir: Path, output_job_dir: Path,
) -> dict[str, Any]:
    """Complete V2 chirp_independent ONLY from acoustic PASS + identical FLAC.

    Historical costs belong to the historical job; V2 reuse incurs 0 new ASR
    charges. This does not prove source speech coverage anew, and the final
    Golden QA must still check source material.
    """
    if claim.get("stage") != "chirp_independent":
        raise V2Conflict("only chirp_independent stage may reuse Chirp")
    source = historical_job_dir / "normalized.flac"
    if not source.is_file() or file_sha256(source) != claim.get("source_sha"):
        raise ChirpReuseError("normalized source SHA differs from the V2 claim")
    merged_path = historical_job_dir / "merged-words.json"
    completeness_path = historical_job_dir / "chirp-completeness.json"
    subtitles_path = historical_job_dir / "subtitles.json"
    plan_path = historical_job_dir / "chunk-plan.json"
    submission_path = historical_job_dir / "chirp-submitted.json"
    evidence_paths = (merged_path, completeness_path, subtitles_path, plan_path, submission_path)
    merged, gate, subs, plan, submitted = tuple(_read(p) for p in evidence_paths)
    if gate.get("status") != "PASS" or gate.get("handoff_allowed") is not True:
        raise ChirpReuseError("Chirp acoustic completeness was not approved")
    if gate.get("blockers"):
        raise ChirpReuseError("Chirp has unresolved audible blockers")
    words = merged.get("words")
    segments = subs.get("segments")
    if not isinstance(words, list) or not words or not isinstance(segments, list) or not segments:
        raise ChirpReuseError("missing immutable Chirp words or subtitle segments")
    try:
        plan_duration = float(plan["duration_seconds"])
        measured = duration_seconds(source)
    except (KeyError, ValueError, TypeError) as exc:
        raise ChirpReuseError("retained Chirp plan has invalid duration") from exc
    if abs(plan_duration - measured) > 0.15:
        raise ChirpReuseError("retained Chirp plan duration differs from normalized audio")
    previous_start = -1
    for item in words:
        if not isinstance(item, dict) or not str(item.get("word", "")):
            raise ChirpReuseError("invalid or empty Chirp word evidence")
        try:
            start, end = int(item["start_ms"]), int(item["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ChirpReuseError("invalid Chirp timestamp evidence") from exc
        if start < previous_start or end <= start or end > measured*1000 + 300:
            raise ChirpReuseError("impossible or unsorted Chirp timing")
        previous_start = start
    if not submitted:
        raise ChirpReuseError("no submitted Chirp operation evidence")
    report: dict[str, Any] = {
        "schema": "golden-v2-chirp-evidence-reuse-v1",
        "job_id": claim["job_id"],
        "normalized_source_sha256": claim["source_sha"],
        "source_duration_seconds": measured,
        "existing_chirp_completeness": "PASS",
        "word_count": len(words),
        "historical_provider_cost_not_rebilled": True,
        "new_provider_calls": 0,
        "new_chirp_actual_cents": 0,
        "chirp_word_timing_authority": True,
        "artifact_sha256": {
            p.name: file_sha256(p) for p in evidence_paths
        },
        "original_provider_operation_details": "RETAINED_IN_HISTORICAL_JOB",
        "requires_independent_v2_golden_qa": True,
    }
    path = output_job_dir / "golden-v2" / "chirp-reuse.json"
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        wire = json.dumps(previous, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if wire != encoded:
            raise ChirpReuseError("Chirp reuse manifest differs from prior evidence")
    else:
        _atomic_json(path, report)
    ledger.complete(claim, evidence_sha=digest, actual_cents=0)
    return report
