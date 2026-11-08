"""Offline end-to-end local worker acceptance with real audio, fake ASR evidence."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import subprocess
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_coordinator import advance_local, run_ready_local
from app.providers.golden_v2_audio_staging import file_sha256


def _wire(doc):
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class CoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audio_fixture = TemporaryDirectory()
        original = Path(cls.audio_fixture.name) / "original.wav"
        cls.flac = Path(cls.audio_fixture.name) / "normalized.flac"
        with wave.open(str(original), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            frames = bytearray()
            for sample in range(8 * 16000):
                sec = sample / 16000
                amplitude = int(10000 * math.sin(2 * math.pi * 300 * sec)) if 2 <= sec < 4 else 0
                frames.extend(struct.pack("<h", amplitude))
            out.writeframes(frames)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(original),
                        "-c:a", "flac", "-y", str(cls.flac)], check=True,
                       timeout=30, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.audio_fixture.cleanup()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.job_dir = Path(self.temp.name) / "job"
        self.job_dir.mkdir()
        self.v2dir = self.job_dir / "golden-v2"
        self.v2dir.mkdir()
        (self.job_dir / "normalized.flac").write_bytes(self.flac.read_bytes())
        self.sha = file_sha256(self.flac)
        self.store = GoldenV2Ledger(Path(self.temp.name) / "ledger.db")

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, *, mode="SMART_2PLUS", primary_text="復次"):
        job_id = self.store.create(source_sha=self.sha, mode=mode,
                                   estimated_stage_cents={
                                       "gemini_primary": 1, "chirp_independent": 1,
                                       "compare": 0, "third_optional": 1,
                                       "adjudication": 0, "golden_qa": 0})
        self.store.approve(job_id, revision=0, approved_cents=3,
                           reference="offline-test", actor="test-owner")
        for name, filename, doc in (
            ("gemini_primary", "primary-stage.json", {
                "schema": "golden-v2-primary-stage-v1", "job_id": job_id,
                "source_sha256": self.sha, "ready_for_chirp": True,
                "assembled": {"text": primary_text}}),
            ("chirp_independent", "chirp-merged.json", {
                "schema": "golden-v2-chirp-merged-v1", "job_id": job_id,
                "source_sha": self.sha, "duration_ms": 8000,
                "timing_authority": "chirp_3",
                "words": [{"word": "復次", "start_ms": 2050, "end_ms": 3900}]}),
        ):
            claim = self.store.claim_next(job_id, worker_id="offline-test")
            self.assertEqual(claim["stage"], name)
            wire = _wire(doc)
            (self.v2dir / filename).write_bytes(wire)
            self.store.complete(claim, evidence_sha=hashlib.sha256(wire).hexdigest(),
                                actual_cents=0)
        return job_id

    def test_consistent_smart_skips_third_and_completes_local_qa(self):
        job = self.seed()
        actions = run_ready_local(ledger=self.store, job_id=job, job_dir=self.job_dir)
        self.assertEqual([item["action"] for item in actions], [
            "COMPARISON_COMPLETE", "ADJUDICATION_COMPLETE", "QA_COMPLETE",
            "AWAITING_FINAL_REVIEW",
        ])
        state = self.store.status(job)
        self.assertEqual(state["status"], "AWAITING_FINAL_REVIEW")
        self.assertEqual(next(row["state"] for row in state["stages"]
                              if row["name"] == "third_optional"), "SKIPPED")
        for filename in ("final.srt", "final-corrected-transcript.txt",
                         "render-manifest.json", "golden-qa.json"):
            self.assertTrue((self.v2dir / filename).is_file(), filename)
        self.assertTrue(all(action.get("provider_calls_started") is not True
                            for action in actions))

    def test_chirp_first_skipped_primary_does_not_claim_compare(self):
        job = self.seed()
        with self.store._tx() as db:
            db.execute("UPDATE v2_stages SET state='SKIPPED' WHERE job_id=? "
                       "AND name='gemini_primary'", (job,))
        before = self.store.status(job)
        response = advance_local(ledger=self.store, job_id=job, job_dir=self.job_dir)
        self.assertEqual(response['action'], 'CHIRP_FIRST_REFERENCE_ALIGN_REQUIRED')
        after = self.store.status(job)
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(next(s['state'] for s in after['stages']
                              if s['name'] == 'compare'), 'PENDING')

    def test_chirp_first_existing_compare_claim_is_not_retried(self):
        job = self.seed()
        self.store.claim_next(job, worker_id='prior-worker')
        with self.store._tx() as db:
            db.execute("UPDATE v2_stages SET state='SKIPPED' WHERE job_id=? "
                       "AND name='gemini_primary'", (job,))
        before = self.store.status(job)
        response = advance_local(ledger=self.store, job_id=job, job_dir=self.job_dir,
                                 minimum_recovery_age_seconds=0)
        self.assertEqual(response['action'], 'CHIRP_FIRST_REFERENCE_ALIGN_REQUIRED')
        after = self.store.status(job)
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(next(s['state'] for s in after['stages']
                              if s['name'] == 'compare'), 'RUNNING')


    def test_chirp_first_attested_reference_completes_comparison(self):
        job = self.seed()
        with self.store._tx() as db:
            db.execute("UPDATE v2_stages SET state='SKIPPED' WHERE job_id=? "
                       "AND name='gemini_primary'", (job,))
        ref_path = self.v2dir / "chirp-first-reference.srt"
        ref_path.write_text(
            "1\n00:00:00,000 --> 00:00:08,000\n復次\n", encoding="utf-8")
        packet = {
            "schema": "golden-v2-chirp-first-reference-v1",
            "source_kind": "HUMAN_CORRECTED_SRT",
            "job_id": job, "source_sha256": self.sha,
            "reference_srt_sha256": hashlib.sha256(ref_path.read_bytes()).hexdigest(),
            "reviewer": "owner-fixture", "review_reference": "verified-test-srt",
        }
        (self.v2dir / "chirp-first-reference.json").write_bytes(_wire(packet))
        action = advance_local(ledger=self.store, job_id=job, job_dir=self.job_dir)
        self.assertEqual(action["action"], "COMPARISON_COMPLETE")
        report = json.loads((self.v2dir / "engine-comparison.json").read_text())
        self.assertEqual(report["comparison_reference_kind"], "HUMAN_CORRECTED_SRT")
        self.assertEqual(report["pending_review_count"], 0)
        self.assertIsNone(report["primary_evidence_sha256"])
        self.assertEqual(next(s["state"] for s in self.store.status(job)["stages"]
                              if s["name"] == "compare"), "COMPLETE")


    def test_chirp_first_operator_staging_is_dry_run_then_idempotent(self):
        from scripts.golden_v2_prepare_chirp_first_reference import prepare
        job = self.seed()
        with self.store._tx() as db:
            db.execute("UPDATE v2_stages SET state='SKIPPED' WHERE job_id=? "
                       "AND name='gemini_primary'", (job,))
        source = Path(self.temp.name) / "pinned-manual.srt"
        source.write_text(
            "1\n00:00:00,000 --> 00:00:08,000\n復次\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        opts = dict(ledger=self.store, job_id=job, job_dir=self.job_dir,
                    source_srt=source, expected_srt_sha=digest,
                    reviewer="owner-fixture", review_reference="verified-test-ref")
        dry = prepare(**opts, apply=False)
        self.assertFalse(dry["applied"])
        self.assertFalse((self.v2dir / "chirp-first-reference.json").exists())
        applied = prepare(**opts, apply=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["reference_packet_sha256"],
                         dry["reference_packet_sha256"])
        self.assertEqual(prepare(**opts, apply=True), applied)
        self.assertEqual(advance_local(
            ledger=self.store, job_id=job, job_dir=self.job_dir)["action"],
            "COMPARISON_COMPLETE")

    def test_disputed_flash_waits_for_human_without_claiming_review(self):
        job = self.seed(mode="FLASH_CHIRP", primary_text="復此")
        actions = run_ready_local(ledger=self.store, job_id=job, job_dir=self.job_dir)
        self.assertEqual([item["action"] for item in actions], [
            "COMPARISON_COMPLETE", "HUMAN_AUDIO_REVIEW_REQUIRED",
        ])
        self.assertEqual(next(row["state"] for row in self.store.status(job)["stages"]
                              if row["name"] == "adjudication"), "PENDING")
        self.assertFalse((self.v2dir / "final.srt").exists())

    def test_disputed_smart_requires_provider_third_and_does_not_bill(self):
        job = self.seed(primary_text="復此")
        actions = run_ready_local(ledger=self.store, job_id=job, job_dir=self.job_dir)
        self.assertEqual([item["action"] for item in actions], [
            "COMPARISON_COMPLETE", "PROVIDER_STAGE_REQUIRED",
        ])
        self.assertEqual(self.store.status(job)["spent_cents"], 0)

    def test_recover_stale_local_comparison_but_not_live_or_paid_stage(self):
        job = self.seed()
        old = self.store.claim_next(job, worker_id="crashed-local")
        self.assertEqual(old["stage"], "compare")
        state = self.store.status(job)
        blocked = advance_local(ledger=self.store, job_id=job, job_dir=self.job_dir)
        self.assertEqual(blocked["action"], "RECOVERY_BLOCKED")
        with self.assertRaises(V2Conflict):
            self.store.reclaim_stale_local(job, stage="gemini_primary",
                                           worker_id="new", expected_revision=state["revision"],
                                           minimum_age_seconds=0)
        with sqlite3.connect(self.store.database) as db:
            db.execute("UPDATE v2_stages SET updated_at=? WHERE job_id=? AND name='compare'",
                       ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), job))
        outcome = advance_local(ledger=self.store, job_id=job, job_dir=self.job_dir)
        self.assertEqual(outcome["action"], "COMPARISON_COMPLETE")
        with self.assertRaises(V2Conflict):
            self.store.complete(old, evidence_sha="f" * 64, actual_cents=0,
                                comparison_has_dispute=False)

    def test_revision_cas_rejects_stale_recovery(self):
        job = self.seed()
        self.store.claim_next(job, worker_id="worker-A")
        current = self.store.status(job)
        with self.assertRaisesRegex(V2Conflict, "revision"):
            self.store.reclaim_stale_local(job, stage="compare", worker_id="worker-B",
                                           minimum_age_seconds=0,
                                           expected_revision=current["revision"] - 1)


if __name__ == "__main__":
    unittest.main()
