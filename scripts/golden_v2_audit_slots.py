"""Read-only Golden V2 legacy paid-audit slot inventory.

Use before changing existing provider-output directories to the newer
source/model/window-keyed REQUEST_STARTED barriers. This command NEVER
copies/deletes paid evidence, calls providers, or changes SQLite.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.providers.gemini_primary_audio import _paid_window_slot_digest


DIRECTORIES = {
    "gemini_primary": ("gemini-primary", "gemini-3.8-flash"),
    "third_optional": ("gemini-third", "gemini-3.5-transcribe"),
}
WINDOW_KEYS = ("index", "start_seconds", "end_seconds", "gcs_uri", "mime_type")


def audit_paid_window_slots(job_dir: Path) -> dict[str, Any]:
    """Classify legacy receipts, ambiguous records and duplicate slot claims.

    A receipt is never silently relabeled COMPLETE, and the returned report
    does not contain provider response text, source prompt or credentials.
    """
    job_dir = Path(job_dir)
    results = []
    for stage, (directory, expected_model) in DIRECTORIES.items():
        folder = job_dir / directory
        groups: dict[str, list[dict[str, Any]]] = {}
        errors: list[dict[str, Any]] = []
        if folder.is_symlink():
            errors.append({"file": directory, "issue": "SYMLINKED_AUDIT_FOLDER"})
        elif folder.exists() and not folder.is_dir():
            errors.append({"file": directory, "issue": "NOT_DIRECTORY"})
        elif folder.is_dir():
            for path in sorted(folder.glob("*.json")):
                if path.is_symlink():
                    errors.append({"file": path.name, "issue": "SYMLINKED_RECEIPT"})
                    continue
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(record, dict):
                        raise ValueError("record must be an object")
                    identity = record["identity"]
                    if (not isinstance(identity, dict)
                            or identity.get("model") != expected_model
                            or not isinstance(identity.get("source_sha256"), str)
                            or len(identity["source_sha256"]) != 64
                            or not isinstance(identity.get("window"), dict)
                            or any(key not in identity["window"] for key in WINDOW_KEYS)):
                        raise ValueError("missing or unsupported receipt identity")
                    slot = _paid_window_slot_digest(identity)
                except (OSError, ValueError, TypeError, KeyError):
                    errors.append({"file": path.name, "issue": "UNREADABLE_OR_UNBOUND_RECEIPT"})
                    continue
                groups.setdefault(slot, []).append({
                    "file": path.name,
                    "status": str(record.get("status", "UNKNOWN")),
                    "canonical_name": f"{slot}.json",
                    "has_raw_response": "raw_response" in record,
                    "has_usage": "usage_metadata" in record,
                })
        for slot, records in sorted(groups.items()):
            if len(records) > 1:
                verdict = "COLLISION_RECONCILIATION_REQUIRED"
            elif records[0]["status"] != "COMPLETE":
                verdict = "NONCOMPLETE_RECONCILIATION_REQUIRED"
            elif not records[0]["has_raw_response"] or not records[0]["has_usage"]:
                verdict = "INCOMPLETE_EVIDENCE_RECONCILIATION_REQUIRED"
            elif records[0]["file"] != records[0]["canonical_name"]:
                verdict = "LEGACY_COMPLETE_REINDEX_REQUIRED"
            else:
                verdict = "CANONICAL_COMPLETE"
            results.append({
                "stage": stage, "slot_digest": slot, "verdict": verdict,
                "record_count": len(records),
                "records": records,
            })
        for error in errors:
            results.append({
                "stage": stage, "slot_digest": None,
                "verdict": "UNINSPECTABLE_RECONCILIATION_REQUIRED",
                "record_count": 1, "records": [error],
            })
    blockers = [r for r in results if r["verdict"] != "CANONICAL_COMPLETE"]
    return {
        "schema": "golden-v2-paid-slot-inventory-v1",
        "read_only": True,
        "provider_calls": 0,
        "slot_count": len(results),
        "blocker_count": len(blockers),
        "safe_to_reuse_without_manual_reindex": not blockers,
        "items": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only pre-cutover paid-window audit")
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args()
    report = audit_paid_window_slots(args.job_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["blocker_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
