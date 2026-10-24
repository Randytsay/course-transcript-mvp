"""Provider-free review draft from a held, source-bound Chirp merge candidate.

This is intentionally not an AI-reviewed certificate, Golden QA, or publication.
It preserves every recognized word and its exact derived Chirp boundaries,
reports missing semantic/acoustic QA, and never modifies the stage ledger.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_quote_intake import wire
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_versioned_artifacts import write_versioned, resolve_versioned
from app.subtitles.golden_v2_render import render_golden_v2


def prepare_ai_draft(
    *, root: Path, job_id: str, candidate_sha256: str,
    acoustic_sha256: str, expected_revision: int,
) -> dict:
    root = Path(root)
    if (not re.fullmatch(r"v2p_[0-9a-f]{32}", job_id)
            or not re.fullmatch(r"[0-9a-f]{64}", candidate_sha256 or "")
            or not re.fullmatch(r"[0-9a-f]{64}", acoustic_sha256 or "")
            or type(expected_revision) is not int
            or root.is_symlink() or (root/"jobs").is_symlink()):
        raise V2Conflict("invalid AI draft request identity")
    job = root/"jobs"/job_id
    folder = job/"golden-v2"
    if job.is_symlink() or folder.is_symlink():
        raise V2Conflict("unsafe source-bound AI draft directory")
    ledger = GoldenV2Ledger.open_existing(root/"ledger.sqlite")
    state = ledger.status(job_id)
    chirp = next(s for s in state["stages"] if s["name"]=="chirp_independent")
    if (state["revision"] != expected_revision
            or state["status"] != "PROCESSING"
            or state["approved_cents"] is None
            or chirp["state"] != "RUNNING"):
        raise V2Conflict("only approved, active Chirp review candidates are eligible")
    rows = ledger.chirp_windows(job_id)
    if not rows or any(row["status"]!="DONE" or not row["result_sha"] for row in rows):
        raise V2Conflict("all provider operation outputs must be saved and reconciled")

    candidate_path=folder/f"chirp-merged-candidate-{candidate_sha256[:20]}.json"
    acoustic_path=folder/f"chirp-acoustic-qa-{acoustic_sha256[:20]}.json"
    if (candidate_path.is_symlink() or acoustic_path.is_symlink()
            or not candidate_path.is_file() or not acoustic_path.is_file()
            or candidate_path.stat().st_size > 64*1024*1024
            or acoustic_path.stat().st_size > 16*1024*1024
            or file_sha256(candidate_path) != candidate_sha256
            or file_sha256(acoustic_path) != acoustic_sha256):
        raise V2Conflict("immutable Chirp review artifacts missing or changed")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    acoustic = json.loads(acoustic_path.read_text(encoding="utf-8"))
    if (candidate.get("schema") != "golden-v2-chirp-merged-v1"
            or candidate.get("job_id") != job_id
            or candidate.get("source_sha") != state["source_sha"]
            or candidate.get("timing_authority") != "chirp_3"
            or candidate.get("golden_pass") is not False
            or candidate.get("acoustic_timing_verified") is not False
            or candidate.get("window_result_shas") != [
                row["result_sha"] for row in rows if row.get("purpose","BASE")=="BASE"]
            or acoustic.get("status") != "REVIEW"
            or acoustic.get("source_sha256") != state["source_sha"]
            or acoustic.get("merged_sha256") != candidate_sha256):
        raise V2Conflict("AI draft source, operation ancestry, or acoustic hold changed")

    rendered = render_golden_v2(merged=candidate, replacements=[],
                                punctuation_after={})
    output = folder/"ai-draft.srt"
    transcript = folder/"ai-draft-transcript.txt"
    srt_sha, _ = write_versioned(output, rendered["srt"].encode("utf-8"))
    txt_sha, _ = write_versioned(
        transcript, rendered["transcript"].encode("utf-8"))
    manifest = {
        "schema": "golden-v2-ai-draft-v1",
        "quality_tier": "AI_DRAFT_REVIEW_REQUIRED",
        "job_id": job_id,
        "source_sha256": state["source_sha"],
        "candidate_sha256": candidate_sha256,
        "acoustic_sha256": acoustic_sha256,
        "provider_result_shas": [row["result_sha"] for row in rows],
        "word_count": len(candidate["words"]),
        "cue_count": len(rendered["cue_manifest"]),
        "srt_sha256": srt_sha, "transcript_sha256": txt_sha,
        "acoustic_gate": "REVIEW", "semantic_gate": "REVIEW",
        "human_listening": "NOT_CLAIMED", "golden_acceptance": "NOT_CLAIMED",
        "publication_allowed": False, "provider_calls_started": False,
        "drive_mutations_started": False,
    }
    digest, selected = write_versioned(folder/"ai-draft-manifest.json",
                                       wire(manifest).encode("utf-8"))
    return {
        "job_id": job_id, "quality_tier": "AI_DRAFT_REVIEW_REQUIRED",
        "word_count": manifest["word_count"], "cue_count": manifest["cue_count"],
        "srt_sha256": srt_sha, "transcript_sha256": txt_sha,
        "draft_manifest_sha256": digest, "acoustic_gate": "REVIEW",
        "semantic_gate": "REVIEW", "publication_allowed": False,
        "provider_calls_started": False, "drive_mutations_started": False,
    }
