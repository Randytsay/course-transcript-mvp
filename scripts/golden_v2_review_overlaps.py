"""Trusted local operator CLI for Gemini overlap-seam audio review.

Inspect is read-only. Apply requires a local OS-authenticated operator who has
actually listened to each specified interval and recorded decisions. Neither
command starts providers, publishes files, or asserts whole-file Golden QA.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.jobs.golden_v2_read import get_v2_status
from app.pipeline.golden_v2_primary_stage import (
    finalize_reviewed_primary_overlaps, verify_primary_overlap_artifacts,
)
from app.providers.golden_v2_audio_staging import file_sha256


def inspect_pending_overlaps(
    *, database: Path, job_id: str, job_dir: Path, normalized_audio: Path,
) -> dict[str, Any]:
    """Inspect immutable source/ledger binding without opening SQLite for write."""
    status = get_v2_status(database, job_id=job_id)
    if not status or status["job"]["status"] != "NEEDS_REVIEW":
        raise V2Conflict("no pending V2 overlap review")
    first = status["stages"][0]
    if first["name"] != "gemini_primary" or first["state"] != "REVIEW_REQUIRED":
        raise V2Conflict("review hold is not the Gemini primary stage")
    if (not normalized_audio.is_file() or
            file_sha256(normalized_audio) != status["job"]["source_sha"]):
        raise V2Conflict("normalized audio differs from approved job source")
    report, records = verify_primary_overlap_artifacts(
        job_dir=job_dir, job_id=job_id,
        source_sha=status["job"]["source_sha"],
        original_sha=first["evidence_sha"],
    )
    windows = report["windows"]
    reviews = report["assembled"]["review_items"]
    texts = [record["text"].strip() for record in records]
    items: list[dict[str, Any]] = []
    for item in reviews:
        pair = item.get("between_windows")
        if (item.get("reason") != "overlap_audio_alignment_required"
                or not isinstance(pair, list) or len(pair) != 2
                or any(type(i) is not int for i in pair)
                or pair[1] != pair[0] + 1 or not 0 <= pair[0] < len(windows) - 1):
            raise V2Conflict("unexpected primary hold type or seam ordering")
        n = pair[0]
        left, right = windows[n], windows[n + 1]
        start_ms = round(float(right["start_seconds"]) * 1000)
        end_ms = round(float(left["end_seconds"]) * 1000)
        if start_ms >= end_ms:
            raise V2Conflict("invalid overlap timing")
        items.append({
            "between_windows": [n, n + 1],
            "audio_start_ms": start_ms, "audio_end_ms": end_ms,
            "left_window_text_tail": texts[n][-400:],
            "right_window_text_head": texts[n + 1][:400],
            "left_window_text_chars": len(texts[n]),
            "right_window_text_chars": len(texts[n + 1]),
        })
    return {
        "job_id": job_id, "revision": status["job"]["revision"],
        "original_evidence_sha256": first["evidence_sha"],
        "normalized_audio": str(normalized_audio.resolve()),
        "normalized_audio_sha256": status["job"]["source_sha"],
        "review_scope": "OVERLAP_SEAMS_ONLY",
        "whole_file_qa": "UNVERIFIED", "seams": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--normalized-audio", type=Path, required=True)
    parser.add_argument("--decisions-json", type=Path,
                        help="Apply human-reviewed JSON seam decisions; omit for read-only inspection")
    parser.add_argument("--reviewer", help="Local operator identity, required with decisions")
    parser.add_argument("--review-reference", help="Independent listening evidence reference")
    parser.add_argument("--confirm-listened", action="store_true",
                        help="Operator explicitly attests all listed intervals were heard")
    args = parser.parse_args()
    try:
        inspection = inspect_pending_overlaps(
            database=args.database, job_id=args.job_id, job_dir=args.job_dir,
            normalized_audio=args.normalized_audio,
        )
        if args.decisions_json is None:
            if args.confirm_listened or args.reviewer or args.review_reference:
                parser.error("reviewer flags require --decisions-json")
            result = inspection
        else:
            if not args.confirm_listened or not args.reviewer or not args.review_reference:
                parser.error("apply requires --confirm-listened, --reviewer and --review-reference")
            decisions = json.loads(args.decisions_json.read_text(encoding="utf-8"))
            if not isinstance(decisions, list):
                raise ValueError("decisions must be a JSON array")
            result = finalize_reviewed_primary_overlaps(
                ledger=GoldenV2Ledger(args.database), job_id=args.job_id,
                revision=inspection["revision"],
                previous_evidence_sha=inspection["original_evidence_sha256"],
                job_dir=args.job_dir, normalized_audio=args.normalized_audio,
                seam_decisions=decisions, reviewer=args.reviewer,
                review_reference=args.review_reference,
            )
    except (V2Conflict, ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.error(f"review remains blocked: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
