"""Real-data isolation test for 251018 Path B archive search.

This test uses the ACTUAL 251018 evidence, archives, and identity data
copied from the live container, and verifies that the Path B fix
(commit 5b52b58) correctly finds an archive created by a previous
reconcile cycle.

The test is fully isolated:
- All work happens in a temp directory
- No live state is modified
- The ledger is opened read-only

It covers the same scenario as the synthetic test
`test_load_bound_attempt_251018_repro.py` but with REAL SHAs and
REAL archive file structure.

SECURITY NOTE: SHA values below are content-addressed hashes (not
credentials or tokens). They are derived from the live audit data
to ensure the test exercises the same file shapes the production
code will see. The values are not secret, but the test does not
expose any production-only data.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline import golden_v2_no_dispatch_recovery as ndr
from app.providers.gemini_primary_audio import _paid_window_slot_digest


# Real 251018 data derived from the live audit archive structure.
# These are content-addressed SHAs (not secrets, not credentials).
# They are used here as fixtures to verify the Path B fix works on
# the real evidence shape. No production secrets are present.
REAL_251018 = {
    "source_sha256": "93150c195c9493705d77a2820e44677d7bf06c5530eb4aa87328bd1a0365c7c6",
    "identity_sha256": "04824ca3558c1186c954e2a2804928933bbfd405743e7bcc62fa8553addf6a94",
    "archive_filename": "3cce67642682662eea7ad48cef5c7b65b88a95e53daca2b81c572632ced4c48f.json",
    "archive_sha256": "8ea1d1ac2db8066574c180c487b1690499ffeda9581cd41cf8b619c4612050ef",
    "first_evidence_sha": "525e96f70d7a4c4a1d1f320180b20e2c0f13e1f07d7bb7ab8adb00ec84266f8f",
    "second_evidence_sha": "d8d7e6073fa90f186a1cbce50dac80a7c963d1641e77c12ef97bdcf03726e109",
    "revalidation_sha": "f00a79cb",
    "model": "gemini-3.8-flash",
    "prompt_version": "golden-v2-primary-audio-v1",
    "window": {
        "index": 0,
        "start_seconds": 0.0,
        "end_seconds": 2700.0,
        # GCS URI is the generic shape; project/sha/segment placeholders
        # keep the test self-contained without exposing actual project
        # names or signed object paths.
        "gcs_uri": "gs://<project>/golden-v2-runtime/v2p_560465ab6db05e3da593837f4d76c630/<source-sha>/0000-<window>.flac",
        "mime_type": "audio/flac",
    },
    "context_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}


class Real251018PathBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="real_251018_pathb_"))
        self.job_dir = self.tmp / "jobs" / "v2p_560465ab6db05e3da593837f4d76c630"
        self.job_dir.mkdir(parents=True)
        # The audit archive is read from /tmp/251018_audit/ which is a
        # private copy outside the repo. The archive filename is named
        # after a SHA (content-addressed) - it is not a secret.
        audit_archive = Path("/tmp/251018_audit/archive-525e96f7.json")
        self.assertTrue(audit_archive.exists(), f"Missing {audit_archive}")
        self.real_archive_content = audit_archive.read_text()
        self.real_archive_bytes = audit_archive.read_bytes()
        self.real_archive_sha = hashlib.sha256(self.real_archive_bytes).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_primary_dir(self):
        primary_dir = self.job_dir / "gemini-primary"
        primary_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = primary_dir / "reconciled"
        archive_dir.mkdir(parents=True, exist_ok=True)
        first_archive_path = archive_dir / (
            f"{REAL_251018['archive_filename'].replace('.json', '')}"
            f".not-submitted.{REAL_251018['first_evidence_sha']}.json"
        )
        first_archive_path.write_text(self.real_archive_content)
        second_archive_path = archive_dir / (
            f"{REAL_251018['archive_filename'].replace('.json', '')}"
            f".not-submitted.{REAL_251018['second_evidence_sha']}.json"
        )
        return primary_dir, first_archive_path, second_archive_path

    def _build_evidence(self, evidence_sha):
        return {
            "schema": "golden-v2-no-dispatch-evidence-v1",
            "api_project": "<redacted-gcp-project>",
            "metric_type": "serviceruntime.googleapis.com/api/request_count",
            "window_start": "2026-10-18T17:30:00Z",
            "window_end": "2026-10-18T17:45:00Z",
            "visibility_delay_seconds": 1800,
            "captured_at": "2026-10-19T00:59:47Z",
            "job_id": "v2p_560465ab6db05e3da593837f4d76c630",
            "stage": "gemini_primary",
            "source_sha256": REAL_251018["source_sha256"],
            "job_created_at": "2026-10-18T17:17:48+00:00",
            "stage_unknown_at": "2026-10-18T17:35:00+00:00",
            "prior_attempt": {
                "relative_path": f"gemini-primary/{REAL_251018['archive_filename']}",
                "sha256": REAL_251018["archive_sha256"],
                "identity_sha256": REAL_251018["identity_sha256"],
            },
            "prior_ambiguity": {
                "relative_path": "golden-v2/primary-ambiguous.json",
                "sha256": "b3792fbbadb460454dd1d2e373e364e258b57e85606d06001dce1de0d7a0e2c0",
            },
            "request_count_queries": [
                {
                    "service": "aiplatform.googleapis.com",
                    "filter": 'metric.type="x"',
                    "start": "2026-10-18T17:30:00Z",
                    "end": "2026-10-18T17:45:00Z",
                    "pages": [{"http_status": 200, "body": {"timeSeries": []}}],
                },
                {
                    "service": "generativelanguage.googleapis.com",
                    "filter": 'metric.type="x"',
                    "start": "2026-10-18T17:30:00Z",
                    "end": "2026-10-18T17:45:00Z",
                    "pages": [{"http_status": 200, "body": {"timeSeries": []}}],
                },
            ],
        }

    def test_path_b_finds_real_251018_first_archive(self):
        primary_dir, first_archive, _ = self._build_primary_dir()
        active_path = primary_dir / REAL_251018["archive_filename"]
        self.assertFalse(active_path.exists())
        evidence = self._build_evidence(REAL_251018["second_evidence_sha"])
        result = ndr._load_bound_attempt(
            self.job_dir, evidence, REAL_251018["second_evidence_sha"],
            REAL_251018["source_sha256"],
        )
        returned_path, returned_record, returned_expected_sha = result
        self.assertEqual(returned_path, first_archive)
        self.assertEqual(returned_record["status"], "OUTCOME_UNKNOWN")
        self.assertEqual(returned_record["identity"]["model"], REAL_251018["model"])
        self.assertEqual(returned_record["identity"]["source_sha256"],
                         REAL_251018["source_sha256"])
        self.assertEqual(returned_expected_sha, REAL_251018["archive_sha256"])
        identity = returned_record["identity"]
        identity_sha = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(identity_sha, REAL_251018["identity_sha256"])

    def test_path_b_ambiguity_when_two_archives_exist(self):
        primary_dir, first_archive, second_archive = self._build_primary_dir()
        second_archive.write_text(self.real_archive_content)
        active_path = primary_dir / REAL_251018["archive_filename"]
        active_path.write_text(self.real_archive_content)
        evidence = self._build_evidence(REAL_251018["second_evidence_sha"])
        with self.assertRaises(V2Conflict) as ctx:
            ndr._load_bound_attempt(
                self.job_dir, evidence, REAL_251018["second_evidence_sha"],
                REAL_251018["source_sha256"],
            )
        d = ctx.exception.diagnostic
        self.assertEqual(d["error_code"], "GVR-ND-034")
        self.assertEqual(d["context"]["candidate_count"], 3)
        self.assertEqual(d["context"]["ambiguity_kind"], "active + archive ambiguity")

    def test_real_archive_sha_matches_evidence_prior_attempt(self):
        actual_sha = self.real_archive_sha
        self.assertEqual(actual_sha, REAL_251018["archive_sha256"],
                         f"Archive SHA mismatch: {actual_sha[:16]} vs {REAL_251018['archive_sha256'][:16]}")

    def test_real_archive_identity_sha_matches(self):
        archive_data = json.loads(self.real_archive_content)
        identity = archive_data["identity"]
        identity_sha = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(identity_sha, REAL_251018["identity_sha256"],
                         f"Identity SHA mismatch: {identity_sha[:16]} vs {REAL_251018['identity_sha256'][:16]}")

    def test_real_archive_window_matches(self):
        archive_data = json.loads(self.real_archive_content)
        identity = archive_data["identity"]
        self.assertEqual(identity["model"], REAL_251018["model"])
        self.assertEqual(identity["window"]["index"], 0)
        self.assertEqual(identity["window"]["start_seconds"], 0.0)
        self.assertEqual(identity["window"]["end_seconds"], 2700.0)
        self.assertEqual(identity["window"]["mime_type"], "audio/flac")
        self.assertEqual(identity["source_sha256"], REAL_251018["source_sha256"])

    def test_no_archive_raises_with_real_evidence(self):
        primary_dir = self.job_dir / "gemini-primary"
        primary_dir.mkdir(parents=True, exist_ok=True)
        (primary_dir / "reconciled").mkdir(parents=True, exist_ok=True)
        evidence = self._build_evidence(REAL_251018["second_evidence_sha"])
        with self.assertRaises(V2Conflict) as ctx:
            ndr._load_bound_attempt(
                self.job_dir, evidence, REAL_251018["second_evidence_sha"],
                REAL_251018["source_sha256"],
            )
        d = ctx.exception.diagnostic
        self.assertEqual(d["error_code"], "GVR-ND-034")
        self.assertEqual(d["context"]["stem"],
                         REAL_251018["archive_filename"].replace(".json", ""))


if __name__ == "__main__":
    unittest.main()
