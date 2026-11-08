"""Read-only, offline test for source/provider/acoustic settlement preview."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_chirp_reconciliation_preview import (
    preview_chirp_reconciliation,
)


def wire(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


class LocalReconciliationPreviewTests(unittest.TestCase):
    def test_verified_overrun_report_and_corruption_have_no_mutation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = GoldenV2Ledger(root / "ledger.sqlite")
            audio = b"fake-FLAC-bytes"
            source_sha = sha(audio)
            job = ledger.create_from_preflight(
                source_sha=source_sha, mode="FLASH_CHIRP",
                estimated_stage_cents=dict(
                    gemini_primary=240, chirp_independent=78, compare=0,
                    third_optional=0, adjudication=0, golden_qa=0,
                ),
                registration_sha256="d" * 64,
            )
            job_dir = root / "jobs" / job
            job_dir.mkdir(parents=True)
            (job_dir / "normalized.flac").write_bytes(audio)
            ledger.approve(job, revision=0, approved_cents=600,
                           reference="owner-offline", actor="owner-test")
            gemini = ledger.claim_next(job, worker_id="test")
            ledger.complete(gemini, evidence_sha="a" * 64, actual_cents=8)
            chirp = ledger.claim_next(job, worker_id="test")
            row = dict(
                window_id="chunk-0000", source_sha=source_sha,
                clip_sha="c" * 64, gcs_uri="gs://fake/input",
                start_ms=0, end_ms=12000,
            )
            ledger.plan_chirp_window(chirp, row)
            ledger.begin_chirp_submit(chirp, row["window_id"])
            ledger.record_chirp_submission(
                chirp, row["window_id"], "operations/verified-test")
            ledger.recover_submitted_chirp(
                job, row["window_id"], operation_name="operations/verified-test",
                evidence_sha=sha(b"raw-result"), actual_cents=136,
                verifier=lambda **_: True,
            )
            saved = ledger.chirp_windows(job)[0]
            golden = job_dir / "golden-v2"
            window_dir = golden / "chirp-evidence" / row["window_id"]
            window_dir.mkdir(parents=True)
            (window_dir / "provider-raw.json").write_bytes(b"raw-result")
            receipt = window_dir / "window.json"
            receipt.write_bytes(wire(dict(
                source_sha=saved["source_sha"], clip_sha=saved["clip_sha"],
                raw_sha=saved["result_sha"],
                operation_name=saved["operation_name"],
                request_id=saved["request_id"],
                start_ms=saved["start_ms"], end_ms=saved["end_ms"],
                actual_cents=saved["actual_cents"],
            )))
            candidate = wire(dict(source_sha=source_sha, words=[]))
            candidate_sha = sha(candidate)
            (golden / f"chirp-merged-candidate-{candidate_sha[:20]}.json").write_bytes(
                candidate)
            report = wire(dict(
                schema="golden-v2-acoustic-qa-v1",
                status="REVIEW", source_sha256=source_sha,
                merged_sha256=candidate_sha,
                gaps_examined=[{} for _ in range(9)],
                repair_plan={"intervals": [dict(start_ms=100, end_ms=200)]},
            ))
            (golden / f"chirp-acoustic-qa-{sha(report)[:20]}.json").write_bytes(report)
            before = sha((root / "ledger.sqlite").read_bytes())
            result = preview_chirp_reconciliation(
                artifact_root=root, job_id=job)
            after = sha((root / "ledger.sqlite").read_bytes())
            self.assertEqual(before, after)
            self.assertEqual(result["blockers"], [])
            self.assertTrue(result["local_evidence_reconciled"])
            self.assertFalse(result["live_gcp_provider_status_verified"])
            self.assertFalse(result["golden_qa_pass"])
            self.assertTrue(result["read_only"])
            self.assertEqual(result["operation_count"], 1)
            self.assertEqual(result["chirp_window_known_actual_cents"], 136)
            self.assertEqual(result["other_stage_known_actual_cents"], 8)
            self.assertEqual(result["known_total_actual_cents"], 144)
            self.assertEqual(result["acoustic_reports"][0]["gaps_examined"], 9)
            receipt.write_bytes(b'{"broken":true}')
            tampered = preview_chirp_reconciliation(
                artifact_root=root, job_id=job)
            self.assertIn("PROVIDER_RECEIPT_OR_SHA_MISMATCH", tampered["blockers"])
            (job_dir / "normalized.flac").write_bytes(b"changed-source")
            tampered = preview_chirp_reconciliation(
                artifact_root=root, job_id=job)
            self.assertIn("NORMALIZED_SOURCE_SHA_MISMATCH", tampered["blockers"])

    def test_rejects_noncanonical_job_before_file_access(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                preview_chirp_reconciliation(
                    artifact_root=Path(temp), job_id="../../ledger.sqlite")


if __name__ == "__main__":
    unittest.main()
