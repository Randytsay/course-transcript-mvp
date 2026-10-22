"""Provider-free primary content repair artifacts; never a paid/ledger entrypoint.

A repair receipt is a *new*, checksum-pinned dispatcher attestation around an
unchanged native Gemini raw audit. Its request stores the actual source clip,
prompt/config and usage-derived cost (not a cloud invoice). Receipt SHA pins
must come from a trusted dispatcher; hashes cannot authenticate a provider.

Native window indices/identities are retained, even when assembly order changes.
No receipt is manufactured, no source/raw/report is written and no claim is
completed here. Persist returned reports with write_versioned() separately.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import wraps
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.gemini_primary_audio import PrimaryTranscriptError, _paid_window_slot_digest
from app.providers.golden_v2_versioned_artifacts import resolve_versioned

SCHEMA = "golden-v2-primary-content-repair-v1"


def _fail_closed(function):
    @wraps(function)
    def checked(**kwargs):
        try:
            return function(**kwargs)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError,
                IndexError, AttributeError, OverflowError, PrimaryTranscriptError) as exc:
            raise V2Conflict("malformed or missing primary content repair evidence") from exc
    return checked


def _sha(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _read(root: Path, path: Path, digest: str) -> dict[str, Any]:
    if not _sha(digest):
        raise V2Conflict("invalid evidence SHA")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise V2Conflict("symlink in primary content repair evidence")
        if parent == root:
            break
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise V2Conflict("primary content repair evidence checksum mismatch")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise V2Conflict("JSON evidence must be an object")
    return value


def _raw(job_dir: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    return _read(job_dir, job_dir / "gemini-primary" /
                 f"{_paid_window_slot_digest(evidence['identity'])}.json",
                 evidence["raw_audit_sha256"])


def _receipt(job_dir: Path, digest: str, kind: str = "receipts") -> dict[str, Any]:
    if not _sha(digest):
        raise V2Conflict("invalid receipt SHA")
    return _read(job_dir, job_dir / "golden-v2" / f"primary-content-repair-{kind}" /
                 f"{digest}.json", digest)


def _assembly(windows: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    review = [{"reason": "source_bound_seam_receipt_required",
               "between_windows": [i - 1, i]}
              for i in range(1, len(windows))
              if windows[i]["start_seconds"] < windows[i - 1]["end_seconds"]]
    return {"text": "\n\n".join(raw["text"].strip() for raw in records),
            "window_count": len(windows), "structural_coverage": "PASS",
            "acoustic_coverage": "UNVERIFIED", "review_items": review,
            "ready_for_chirp_comparison": not bool(review), "golden_approved": False}


@_fail_closed
def build_primary_content_repair(
    *, job_dir: Path, job_id: str, source_sha: str, original_sha: str,
    replaced_window_index: int, replacement_bounds: list[tuple[float, float]],
    replacement_receipt_shas: list[str], seam_receipt_shas: list[str] | None = None,
) -> dict[str, Any]:
    """Rebuild a candidate from pinned evidence; absent seams preserve ALL text.

    replacement_bounds is the independently approved plan, NOT inferred from
    receipts. New receipt files live under golden-v2/primary-content-repair-
    receipts/<SHA>.json. Original provider audits remain in gemini-primary/.
    """
    job_dir = Path(job_dir)
    original = _read(job_dir, resolve_versioned(job_dir / "golden-v2/primary-stage.json", original_sha), original_sha)
    replacements = [_receipt(job_dir, digest) for digest in replacement_receipt_shas]
    old_evidence = original["window_evidence"]
    kept = [item for i, item in enumerate(old_evidence) if i != replaced_window_index]
    evidence = (old_evidence[:replaced_window_index] +
                [item["raw_evidence"] for item in replacements] +
                old_evidence[replaced_window_index + 1:])
    records = [_raw(job_dir, item) for item in evidence]
    windows = [item["identity"]["window"] for item in evidence]
    assembled = _assembly(windows, records)
    return copy.deepcopy({
        "schema": SCHEMA, "job_id": job_id, "source_sha256": source_sha,
        "mode": "gemini_3_8_audio_first",
        "original_primary_evidence_sha256": original_sha,
        "source_lineage_sha256": original["source_lineage_sha256"],
        "duration_seconds": original["windows"][-1]["end_seconds"],
        "replaced_window_index": replaced_window_index,
        "replacement_bounds": [list(bounds) for bounds in replacement_bounds],
        "retained_window_evidence": kept,
        "replaced_window_evidence": old_evidence[replaced_window_index],
        "replacement_receipt_sha256": replacement_receipt_shas,
        "replacement_receipts": replacements,
        "seam_receipt_sha256": list(seam_receipt_shas or []),
        "windows": windows, "window_evidence": evidence, "assembled": assembled,
        "status": "HOLD_SEAM_EVIDENCE_REQUIRED" if assembled["review_items"] else "READY",
        "ready_for_chirp": assembled["ready_for_chirp_comparison"],
        "golden_approved": False,
    })
