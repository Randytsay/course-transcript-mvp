"""No-cost, read-only migration inventory tests for historical V2 paid receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.providers.gemini_primary_audio import _paid_window_slot_digest
from scripts.golden_v2_audit_slots import audit_paid_window_slots


def identity(model: str = "gemini-3.8-flash") -> dict:
    result = {
        "model": model, "source_sha256": "b" * 64,
        "window": {
            "index": 0, "start_seconds": 0, "end_seconds": 30,
            "gcs_uri": "gs://private-bucket/window.flac",
            "mime_type": "audio/flac",
        },
    }
    if model == "gemini-3.8-flash":
        result.update(prompt_version="old-prompt", context_sha256="a" * 64)
    else:
        result["config"] = {"mode": "VERBATIM"}
    return result


def receipt(path: Path, ident: dict, *, status: str = "COMPLETE") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "identity": ident, "status": status,
        "raw_response": {"provider": "mock"}, "usage_metadata": {"tokens": 1},
    }), encoding="utf-8")


class GoldenV2AuditSlotsTests(unittest.TestCase):
    def test_legacy_paid_record_is_flagged_without_modification(self):
        with TemporaryDirectory() as root:
            job_dir = Path(root)
            ident = identity()
            old_hash = hashlib.sha256(
                json.dumps(ident, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            old_file = job_dir / "gemini-primary" / (old_hash + ".json")
            receipt(old_file, ident)
            before = old_file.read_bytes()
            report = audit_paid_window_slots(job_dir)
            self.assertEqual(report["blocker_count"], 1)
            self.assertEqual(
                report["items"][0]["verdict"], "LEGACY_COMPLETE_REINDEX_REQUIRED",
            )
            self.assertEqual(old_file.read_bytes(), before)
            self.assertEqual(list(old_file.parent.glob("*.json")), [old_file])

    def test_canonical_complete_is_safe_and_noncomplete_is_blocked(self):
        with TemporaryDirectory() as root:
            job_dir = Path(root)
            ident = identity(model="gemini-3.5-transcribe")
            filename = _paid_window_slot_digest(ident) + ".json"
            artifact = job_dir / "gemini-third" / filename
            receipt(artifact, ident)
            self.assertEqual(audit_paid_window_slots(job_dir)["blocker_count"], 0)
            receipt(artifact, ident, status="REQUEST_STARTED")
            item = audit_paid_window_slots(job_dir)["items"][0]
            self.assertEqual(
                item["verdict"], "NONCOMPLETE_RECONCILIATION_REQUIRED",
            )

    def test_collision_and_malformed_receipts_never_assume_no_prior_charge(self):
        with TemporaryDirectory() as root:
            job_dir = Path(root)
            folder = job_dir / "gemini-primary"
            ident = identity()
            receipt(folder / (_paid_window_slot_digest(ident) + ".json"), ident)
            receipt(folder / "historic-original-name.json", ident)
            (folder / "partially-written.json").write_text("{", encoding="utf-8")
            report = audit_paid_window_slots(job_dir)
            verdicts = {row["verdict"] for row in report["items"]}
            self.assertIn("COLLISION_RECONCILIATION_REQUIRED", verdicts)
            self.assertIn("UNINSPECTABLE_RECONCILIATION_REQUIRED", verdicts)
            self.assertEqual(report["blocker_count"], 2)
            self.assertFalse(report["safe_to_reuse_without_manual_reindex"])


if __name__ == "__main__":
    unittest.main()
