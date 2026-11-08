"""Prepare an offline pinned Chirp-first manual SRT snapshot.

No paid APIs, Drive writes, ledger mutations or stage claims. --apply is
explicit; the caller must be an authenticated trusted operator, because
reviewer/review_reference CLI strings are audit metadata, not authentication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_chirp_first_reference import _reference_text_and_extent
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.providers.golden_v2_versioned_artifacts import resolve_versioned


def prepare(*, ledger: GoldenV2Ledger, job_id: str, job_dir: Path,
            source_srt: Path, expected_srt_sha: str, reviewer: str,
            review_reference: str, apply: bool = False) -> dict:
    job_dir = Path(job_dir)
    source_srt = Path(source_srt)
    state = ledger.status(job_id)
    stage = {s["name"]: s for s in state["stages"]}
    if (state["status"] != "PROCESSING"
            or stage["gemini_primary"]["state"] != "SKIPPED"
            or stage["chirp_independent"]["state"] != "COMPLETE"
            or stage["compare"]["state"] not in {"PENDING", "RUNNING"}):
        raise V2Conflict("reference staging requires active Chirp-first compare")
    if (source_srt.is_symlink() or not source_srt.is_file()
            or len(expected_srt_sha) != 64
            or any(c not in "0123456789abcdef" for c in expected_srt_sha)
            or not reviewer.strip() or not review_reference.strip()):
        raise V2Conflict("reference requires exact pinned SRT hash and reviewer")
    raw = source_srt.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_srt_sha:
        raise V2Conflict("manual SRT hash differs from pinned baseline")
    source_sha = state["source_sha"]
    if file_sha256(job_dir / "normalized.flac") != source_sha:
        raise V2Conflict("normalized FLAC differs from approved source")
    chirp_path = resolve_versioned(
        job_dir / "golden-v2" / "chirp-merged.json",
        stage["chirp_independent"]["evidence_sha"])
    if file_sha256(chirp_path) != stage["chirp_independent"]["evidence_sha"]:
        raise V2Conflict("Chirp merge differs from verified ledger stage")
    merged = json.loads(chirp_path.read_bytes())
    if (merged.get("job_id") != job_id
            or merged.get("source_sha") != source_sha
            or merged.get("timing_authority") != "chirp_3"
            or type(merged.get("duration_ms")) is not int):
        raise V2Conflict("Chirp merge has wrong source or timing")
    text, extent, defects = _reference_text_and_extent(raw, merged["duration_ms"])
    packet = {
        "schema": "golden-v2-chirp-first-reference-v1",
        "source_kind": "HUMAN_CORRECTED_SRT",
        "job_id": job_id,
        "source_sha256": source_sha,
        "reference_srt_sha256": expected_srt_sha,
        "reviewer": reviewer.strip(),
        "review_reference": review_reference.strip(),
        "reference_is_acoustic_evidence": False,
    }
    wire = json.dumps(packet, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    outcome = {
        "job_id": job_id, "source_sha256": source_sha,
        "reference_srt_sha256": expected_srt_sha,
        "reference_packet_sha256": hashlib.sha256(wire).hexdigest(),
        "reference_characters": len(text),
        "reference_format_issues": defects,
        "reference_timeline_extent_ratio": round(extent, 6),
        "applied": apply,
    }
    if apply:
        root = job_dir / "golden-v2"
        store_immutable_bytes(root / "chirp-first-reference.srt", raw)
        store_immutable_bytes(root / "chirp-first-reference.json", wire)
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--source-srt", type=Path, required=True)
    parser.add_argument("--expected-srt-sha", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--review-reference", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="Write immutable job-local reference snapshot")
    args = parser.parse_args()
    result = prepare(
        ledger=GoldenV2Ledger(args.ledger), job_id=args.job_id,
        job_dir=args.job_dir, source_srt=args.source_srt,
        expected_srt_sha=args.expected_srt_sha,
        reviewer=args.reviewer, review_reference=args.review_reference,
        apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
