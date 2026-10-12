"""Persistent V2 worker is durable, bounded and provider-free."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES
from app.jobs.golden_v2_read import get_v2_status, list_v2_active_jobs
from app.pipeline.golden_v2_worker import run_v2_worker_pass


def _estimates():
    return {stage: 0 for stage in STAGES}


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


class GoldenV2WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifact_root = self.root / "artifacts"
        self.database = self.artifact_root / "ledger.sqlite"
        self.jobs_root = self.artifact_root / "jobs"
        self.ledger = GoldenV2Ledger(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def create_approved(self, *, mode="QUALITY_3WAY"):
        job_id = self.ledger.create(
            source_sha="a" * 64, mode=mode, estimated_stage_cents=_estimates(),
        )
        self.ledger.approve(
            job_id, revision=0, approved_cents=0,
            reference="owner-approved-test-only", actor="unit-test",
        )
        return job_id

    def pass_worker(self):
        return run_v2_worker_pass(
            database=self.database, artifact_root=self.artifact_root,
            worker_id="worker-test",
        )

    def test_missing_ledger_waits_without_initializing_a_database(self):
        self.database.unlink()
        result = self.pass_worker()
        self.assertEqual(result["action"], "LEDGER_NOT_READY")
        self.assertFalse(self.database.exists())

    def test_rejects_a_ledger_outside_the_v2_artifact_root(self):
        external = self.root / "elsewhere.sqlite"
        external.write_bytes(b"do-not-touch")
        result = run_v2_worker_pass(
            database=external, artifact_root=self.artifact_root,
        )
        self.assertEqual(result["action"], "LEDGER_PATH_REJECTED")
        self.assertEqual(external.read_bytes(), b"do-not-touch")

    def test_unapproved_jobs_are_not_dispatched(self):
        self.ledger.create(
            source_sha="a" * 64, mode="QUALITY_3WAY",
            estimated_stage_cents=_estimates(),
        )
        result = self.pass_worker()
        self.assertEqual(result["jobs_seen"], 0)
        self.assertEqual(result["outcomes"], [])

    def test_approved_job_without_local_artifacts_is_explicitly_blocked(self):
        job_id = self.create_approved()
        result = self.pass_worker()
        self.assertEqual(result["outcomes"][0]["action"],
                         "JOB_ARTIFACT_DIRECTORY_MISSING")
        self.assertEqual(self.ledger.status(job_id)["revision"], 1)

    def test_provider_stage_stays_pending_and_no_provider_call_is_started(self):
        job_id = self.create_approved()
        (self.jobs_root / job_id).mkdir(parents=True)
        result = self.pass_worker()
        self.assertEqual(
            result["outcomes"][0]["actions"][0]["action"],
            "PROVIDER_STAGE_REQUIRED",
        )
        self.assertFalse(result["provider_calls_started"])
        state = self.ledger.status(job_id)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["stages"][0]["state"], "PENDING")

    def test_worker_advances_local_compare_then_holds_for_human_review(self):
        job_id = self.create_approved(mode="FLASH_CHIRP")
        job_dir = self.jobs_root / job_id
        evidence_dir = job_dir / "golden-v2"
        evidence_dir.mkdir(parents=True)
        primary = {
            "schema": "golden-v2-primary-stage-v1", "job_id": job_id,
            "source_sha256": "a" * 64, "ready_for_chirp": True,
            "assembled": {"text": "復此"},
        }
        primary_wire = _wire(primary)
        (evidence_dir / "primary-stage.json").write_bytes(primary_wire)
        claim = self.ledger.claim_next(job_id, worker_id="fake-provider-primary")
        self.ledger.complete(
            claim, evidence_sha=hashlib.sha256(primary_wire).hexdigest(), actual_cents=0,
        )
        chirp = {
            "schema": "golden-v2-chirp-merged-v1", "job_id": job_id,
            "source_sha": "a" * 64, "duration_ms": 8000,
            "timing_authority": "chirp_3",
            "words": [{"word": "復次", "start_ms": 2050, "end_ms": 3900}],
        }
        chirp_wire = _wire(chirp)
        (evidence_dir / "chirp-merged.json").write_bytes(chirp_wire)
        claim = self.ledger.claim_next(job_id, worker_id="fake-provider-chirp")
        self.ledger.complete(
            claim, evidence_sha=hashlib.sha256(chirp_wire).hexdigest(), actual_cents=0,
        )

        result = self.pass_worker()
        self.assertEqual(
            [item["action"] for item in result["outcomes"][0]["actions"]],
            ["COMPARISON_COMPLETE", "HUMAN_AUDIO_REVIEW_REQUIRED"],
        )
        state = self.ledger.status(job_id)
        self.assertEqual(state["status"], "PROCESSING")
        self.assertEqual(state["stages"][2]["state"], "COMPLETE")
        self.assertEqual(state["stages"][4]["state"], "PENDING")
        self.assertFalse(result["provider_calls_started"])
        self.assertFalse(result["drive_mutation_started"])

    def test_source_preflight_ids_are_readable_from_owner_status(self):
        job_id = self.ledger.create_from_preflight(
            source_sha="b" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents=_estimates(), registration_sha256="c" * 64,
        )
        self.assertEqual(get_v2_status(self.database, job_id=job_id)["job"]["id"], job_id)
        self.assertEqual(list_v2_active_jobs(self.database), [])


if __name__ == "__main__":
    unittest.main()
