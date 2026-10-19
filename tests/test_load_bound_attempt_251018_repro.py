"""Offline reproduction test for the 251018 _load_bound_attempt bug.

This test simulates the exact failure scenario for 251018:
1. An initial reconcile cycle runs, creating an archive named
   ``<stem>.not-submitted.<first_evidence_sha>.json``.
2. A second reconcile cycle starts. The on-disk archive is named with
   the FIRST evidence_sha, not the SECOND. The previous version of
   _load_bound_attempt constructed the archive path with the SECOND
   evidence_sha and therefore could not find the archive.
3. The Path B fix uses a glob pattern ``<stem>.not-submitted.*.json``
   and validates by content hash + identity, so the second cycle can
   find the first cycle's archive.

This test NEVER touches the official ledger. It builds a synthetic
``job_dir`` in a temp directory and runs only ``_load_bound_attempt``.
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


def _write_primary_audit(job_dir: Path, source_sha: str) -> Path:
    """Build a minimal OUTCOME_UNKNOWN primary audit file matching
    the same identity that 251018 had (window 0, 2700s, gemini-3.8-flash).
    Returns the path of the written file."""
    identity = {
        "context_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "model": "gemini-3.8-flash",
        "prompt_version": "golden-v2-primary-audio-v1",
        "source_sha256": source_sha,
        "window": {
            "index": 0,
            "start_seconds": 0.0,
            "end_seconds": 2700.0,
            "gcs_uri": "gs://test/0000-abc.flac",
            "mime_type": "audio/flac",
        },
    }
    record = {
        "identity": identity,
        "status": "OUTCOME_UNKNOWN",
        "human_review_required": True,
    }
    slot = _paid_window_slot_digest(identity)
    primary_dir = job_dir / "gemini-primary"
    primary_dir.mkdir(parents=True, exist_ok=True)
    path = primary_dir / f"{slot}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return path, identity, record


def _make_evidence(prior_attempt: dict, source_sha: str) -> dict:
    """Build a minimal evidence dict matching the no-dispatch schema."""
    return {
        "schema": "golden-v2-no-dispatch-evidence-v1",
        "api_project": "test-project",
        "metric_type": "serviceruntime.googleapis.com/api/request_count",
        "window_start": "2026-10-19T00:00:00Z",
        "window_end": "2026-10-19T00:15:00Z",
        "visibility_delay_seconds": 1800,
        "captured_at": "2026-10-19T00:30:00Z",
        "job_id": "v2p_repro",
        "stage": "gemini_primary",
        "source_sha256": source_sha,
        "job_created_at": "2026-10-14T17:17:48+00:00",
        "stage_unknown_at": "2026-10-19T00:05:00+00:00",
        "prior_attempt": prior_attempt,
        "prior_ambiguity": {"relative_path": "golden-v2/primary-ambiguous.json",
                            "sha256": "0" * 64},
        "request_count_queries": [
            {
                "service": "aiplatform.googleapis.com",
                "filter": 'metric.type="x"',
                "start": "2026-10-19T00:00:00Z",
                "end": "2026-10-19T00:15:00Z",
                "pages": [{"http_status": 200, "body": {"timeSeries": []}}],
            },
            {
                "service": "generativelanguage.googleapis.com",
                "filter": 'metric.type="x"',
                "start": "2026-10-19T00:00:00Z",
                "end": "2026-10-19T00:15:00Z",
                "pages": [{"http_status": 200, "body": {"timeSeries": []}}],
            },
        ],
    }


class LoadBoundAttemptReproTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loadbound_"))
        self.job_dir = self.tmp / "jobs" / "v2p_repro"
        self.job_dir.mkdir(parents=True)
        self.source_sha = "9" * 64

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_reconcile_finds_archived_attempt_from_first_cycle(self):
        """251018 reproduction: first cycle archives the active file,
        second cycle must find the archive and validate by content hash."""
        # 1. First reconcile cycle: write active file
        active_path, identity, record = _write_primary_audit(
            self.job_dir, self.source_sha)
        active_sha = hashlib.sha256(active_path.read_bytes()).hexdigest()
        identity_sha = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

        # 2. First cycle: archive moves active → archive named with FIRST evidence_sha
        first_evidence_sha = "525e96f70d7a4c4a1d1f320180b20e2c0f13e1f07d7bb7ab8adb00ec84266f8f"
        first_archive = (self.job_dir / "gemini-primary" / "reconciled" /
                        f"{active_path.stem}.not-submitted.{first_evidence_sha}.json")
        first_archive.parent.mkdir(parents=True, exist_ok=True)
        # Simulate the first reconcile cycle: archive the active file
        # (this is what _archive_attempt does in production).
        first_archive.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True))
        active_path.unlink()  # active → archive rename
        self.assertFalse(active_path.exists())

        # 3. Second cycle: build evidence pointing at same prior_attempt,
        #    but with SECOND evidence_sha (different captured_at etc.)
        second_evidence_sha = "d8d7e6073fa90f186a1cbce50dac80a7c963d1641e77c12ef97bdcf03726e109"
        prior_attempt = {
            "relative_path": f"gemini-primary/{active_path.name}",
            "sha256": active_sha,
            "identity_sha256": identity_sha,
        }
        evidence = _make_evidence(prior_attempt, self.source_sha)
        # The second cycle has its own evidence_sha, but the on-disk
        # archive was named with the first cycle's evidence_sha.

        # 4. _load_bound_attempt should find the archive (via Path B fix)
        #    and return the correct record.
        result = ndr._load_bound_attempt(
            self.job_dir, evidence, second_evidence_sha, self.source_sha)
        returned_path, returned_record, returned_expected_sha = result
        # The returned record should be the archived one
        self.assertEqual(returned_path, first_archive)
        self.assertEqual(returned_record["status"], "OUTCOME_UNKNOWN")
        self.assertEqual(returned_record["identity"]["model"], "gemini-3.8-flash")
        # The expected_sha returned should match what the evidence says
        self.assertEqual(returned_expected_sha, active_sha)

    def test_active_and_archive_both_present_rejected_for_ambiguity(self):
        """If BOTH active and archive exist (shouldn't happen, but
        defensive), the function rejects with anti-double-billing."""
        # Write active file
        active_path, identity, record = _write_primary_audit(
            self.job_dir, self.source_sha)
        active_sha = hashlib.sha256(active_path.read_bytes()).hexdigest()
        identity_sha = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # Also write a "stale" archive with the same content
        archive_dir = self.job_dir / "gemini-primary" / "reconciled"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{active_path.stem}.not-submitted.deadbeef.json"
        archive_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True))
        # Build evidence
        prior_attempt = {
            "relative_path": f"gemini-primary/{active_path.name}",
            "sha256": active_sha,
            "identity_sha256": identity_sha,
        }
        evidence = _make_evidence(prior_attempt, self.source_sha)
        with self.assertRaises(V2Conflict) as ctx:
            ndr._load_bound_attempt(
                self.job_dir, evidence, "new_evidence_sha", self.source_sha)
        d = ctx.exception.diagnostic
        self.assertEqual(d["error_code"], "GVR-ND-034")
        self.assertIn("active + archive ambiguity", str(d["context"]))
        # Anti-double-billing context
        self.assertEqual(d["context"]["candidate_count"], 2)

    def test_no_active_no_archive_raises(self):
        """If neither active nor archive exists, raise with diagnostic."""
        # No files written at all
        active_path, identity, record = _write_primary_audit(
            self.job_dir, self.source_sha)
        # Move active somewhere else (i.e. effectively gone) by deleting
        (self.job_dir / "gemini-primary" / "reconciled").mkdir(parents=True, exist_ok=True)
        active_path.unlink()
        prior_attempt = {
            "relative_path": f"gemini-primary/{active_path.name}",
            "sha256": "0" * 64,
            "identity_sha256": "0" * 64,
        }
        evidence = _make_evidence(prior_attempt, self.source_sha)
        with self.assertRaises(V2Conflict) as ctx:
            ndr._load_bound_attempt(
                self.job_dir, evidence, "any_evidence", self.source_sha)
        d = ctx.exception.diagnostic
        self.assertEqual(d["error_code"], "GVR-ND-034")
        self.assertIn("stem", d["context"])

    def test_archive_with_wrong_sha_rejected(self):
        """If archive exists but its SHA doesn't match the evidence, reject."""
        # First cycle: write active and archive
        active_path, identity, record = _write_primary_audit(
            self.job_dir, self.source_sha)
        active_sha = hashlib.sha256(active_path.read_bytes()).hexdigest()
        identity_sha = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # archive with WRONG content
        archive_dir = self.job_dir / "gemini-primary" / "reconciled"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{active_path.stem}.not-submitted.deadbeef.json"
        archive_path.write_text("WRONG CONTENT")
        # Also delete the active file so only archive remains
        active_path.unlink()
        prior_attempt = {
            "relative_path": f"gemini-primary/{active_path.name}",
            "sha256": active_sha,
            "identity_sha256": identity_sha,
        }
        evidence = _make_evidence(prior_attempt, self.source_sha)
        with self.assertRaises(V2Conflict) as ctx:
            ndr._load_bound_attempt(
                self.job_dir, evidence, "any_evidence", self.source_sha)
        d = ctx.exception.diagnostic
        self.assertEqual(d["error_code"], "GVR-ND-035")

    def test_glob_pattern_includes_actual_archive_name(self):
        """Verify the path B fix's glob actually finds archive files
        with the first-cycle evidence_sha suffix (the bug case)."""
        active_path, identity, record = _write_primary_audit(
            self.job_dir, self.source_sha)
        archive_dir = self.job_dir / "gemini-primary" / "reconciled"
        archive_dir.mkdir(parents=True, exist_ok=True)
        first_sha = "525e96f70d7a4c4a1d1f320180b20e2c0f13e1f07d7bb7ab8adb00ec84266f8f"
        archive = archive_dir / f"{active_path.stem}.not-submitted.{first_sha}.json"
        archive.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True))
        active_path.unlink()
        # Verify the glob finds it
        matches = sorted(archive_dir.glob(f"{active_path.stem}.not-submitted.*.json"))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], archive)


if __name__ == "__main__":
    unittest.main()
