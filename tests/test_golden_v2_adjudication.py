"""Adjudication is source-bound, auditable and resumable without paid requests."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_stage_runner import (
    complete_adjudication_or_hold, complete_comparison, complete_evidence_bound_qa,
)
from app.pipeline.golden_v2_review_bundle import (
    apply_review_bundle, inspect_review_bundle,
)
from app.providers.golden_v2_audio_staging import file_sha256
from app.subtitles.golden_v2_render import _word_evidence


def _wire(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


class V2AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.job_dir = Path(self.temp.name) / "job"
        self.job_dir.mkdir()
        self.v2dir = self.job_dir / "golden-v2"
        self.v2dir.mkdir()
        self.audio = self.job_dir / "normalized.flac"
        source = Path(self.temp.name) / "source.wav"
        with wave.open(str(source), "wb") as wave_file:
            wave_file.setnchannels(1)
            wave_file.setsampwidth(2)
            wave_file.setframerate(16000)
            data = bytearray()
            for i in range(8 * 16000):
                sec = i / 16000
                amp = round(11000 * math.sin(2 * math.pi * 300 * sec)) if 2 <= sec < 4 else 0
                data.extend(struct.pack("<h", amp))
            wave_file.writeframes(data)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(source),
                        "-c:a", "flac", "-y", str(self.audio)], check=True,
                       timeout=30, capture_output=True)
        self.sha = file_sha256(self.audio)
        self.ledger = GoldenV2Ledger(Path(self.temp.name) / "ledger.sqlite")
        self.job = self.ledger.create(
            source_sha=self.sha, mode="FLASH_CHIRP",
            estimated_stage_cents={"gemini_primary": 1, "chirp_independent": 1,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0})
        self.ledger.approve(self.job, revision=0, approved_cents=2,
                            reference="fake-only", actor="test-owner")
        self.words = [{"word": "復次", "start_ms": 2050, "end_ms": 3900}]
        for name, filename, doc in [
            ("gemini_primary", "primary-stage.json", {
                "schema": "golden-v2-primary-stage-v1", "job_id": self.job,
                "source_sha256": self.sha, "ready_for_chirp": True,
                "assembled": {"text": "復此"}}),
            ("chirp_independent", "chirp-merged.json", {
                "schema": "golden-v2-chirp-merged-v1", "job_id": self.job,
                "source_sha": self.sha, "duration_ms": 8000,
                "timing_authority": "chirp_3", "words": self.words}),
        ]:
            claim = self.ledger.claim_next(self.job, worker_id="offline")
            self.assertEqual(claim["stage"], name)
            (self.v2dir / filename).write_bytes(_wire(doc))
            self.ledger.complete(claim, evidence_sha=hashlib.sha256(_wire(doc)).hexdigest(),
                                 actual_cents=0)
        self.comparison = complete_comparison(
            ledger=self.ledger,
            claim=self.ledger.claim_next(self.job, worker_id="offline"),
            job_dir=self.job_dir)
        self.assertEqual(len(self.comparison["candidates"]), 1)
        self.claim = self.ledger.claim_next(self.job, worker_id="offline")
        self.assertEqual(self.claim["stage"], "adjudication")

    def tearDown(self):
        self.temp.cleanup()

    def decision(self, kind="KEEP_CHIRP"):
        report = self.comparison["candidates"][0]
        return {
            "candidate_index": 0, "candidate_sha256": hashlib.sha256(_wire(report)).hexdigest(),
            "source_sha256": self.sha, "status": "RESOLVED", "decision": kind,
            "audio_evidence_checked": True, "reviewed_audio_sha256": self.sha,
            "reviewed_audio_window_ms": [1900, 4100],
            "reviewer": "verified-human", "review_reference": "local-listening-01",
            "decision_reason": "heard the complete original audio window",
        }

    def test_hold_resume_and_complete_without_new_provider_charge(self):
        hold = complete_adjudication_or_hold(
            ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
            reviewer="verified-human", review_reference="local-listening-01")
        self.assertEqual(hold["status"], "REVIEW_REQUIRED")
        self.assertEqual(self.ledger.status(self.job)["status"], "NEEDS_REVIEW")
        self.assertFalse((self.v2dir / "adjudications.json").exists())
        state = GoldenV2Ledger(self.ledger.database)
        with self.assertRaises(V2Conflict):
            state.resume_adjudication_review(
                self.job, revision=state.status(self.job)["revision"],
                reviewer="verified-human", review_reference="local-listening-01",
                previous_evidence_sha="1" * 64)
        state.resume_adjudication_review(
            self.job, revision=state.status(self.job)["revision"],
            reviewer="verified-human", review_reference="local-listening-01",
            previous_evidence_sha=hold["evidence_sha256"])
        resumed = state.claim_next(self.job, worker_id="restarted-offline")
        result = complete_adjudication_or_hold(
            ledger=state, claim=resumed, job_dir=self.job_dir,
            decisions=[self.decision()], reviewer="verified-human",
            review_reference="local-listening-01")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(list(self.v2dir.glob("adjudication-review-*.json")))
        qa = complete_evidence_bound_qa(
            ledger=state, claim=state.claim_next(self.job, worker_id="offline"),
            job_dir=self.job_dir)
        self.assertEqual(qa["status"], "PASS")
        self.assertEqual(state.status(self.job)["status"], "AWAITING_FINAL_REVIEW")
        self.assertFalse(qa["drive_mutation_started"])

    def test_reviewed_word_correction_is_attached_to_exact_candidate(self):
        good = {
            "candidate_index": 0, "start_word": 0, "end_word": 1,
            "corrected_text": "復此", "source_sha256": self.sha,
            "audio_evidence_checked": True, "decision_reason": "reviewed pronunciation",
            "chirp_word_evidence_shas": [_word_evidence(self.words[0], 0, self.sha)],
        }
        bad = {**good, "chirp_word_evidence_shas": ["f" * 64]}
        with self.assertRaisesRegex(V2Conflict, "unaudited source-word correction"):
            complete_adjudication_or_hold(
                ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
                decisions=[self.decision("CORRECT_CHIRP")], replacements=[bad],
                reviewer="verified-human", review_reference="local-listening-01")
        self.assertEqual(self.ledger.status(self.job)["stages"][-2]["state"], "RUNNING")
        complete_adjudication_or_hold(
            ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
            decisions=[self.decision("CORRECT_CHIRP")], replacements=[good],
            reviewer="verified-human", review_reference="local-listening-01")
        qa = complete_evidence_bound_qa(
            ledger=self.ledger, claim=self.ledger.claim_next(self.job, worker_id="offline"),
            job_dir=self.job_dir)
        self.assertEqual(qa["status"], "PASS")
        self.assertIn("復此", (self.v2dir / "final.srt").read_text())

    def test_disk_write_error_does_not_mark_qa_complete(self):
        complete_adjudication_or_hold(
            ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
            decisions=[self.decision()], reviewer="verified-human",
            review_reference="local-listening-01")
        qa_claim = self.ledger.claim_next(self.job, worker_id="offline")
        with patch("app.pipeline.golden_v2_stage_runner._write_immutable_text",
                   side_effect=OSError("simulated disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                complete_evidence_bound_qa(
                    ledger=self.ledger, claim=qa_claim, job_dir=self.job_dir)
        self.assertEqual(self.ledger.status(self.job)["stages"][-1]["state"], "RUNNING")
        self.assertFalse((self.v2dir / "golden-qa.json").exists())
        result = complete_evidence_bound_qa(
            ledger=self.ledger, claim=qa_claim, job_dir=self.job_dir)
        self.assertEqual(result["status"], "PASS")

    def test_review_bundle_is_read_only_and_imports_exact_listened_candidate(self):
        hold = complete_adjudication_or_hold(
            ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
            reviewer="local-reviewer", review_reference="listen-session-01",
        )
        before = hashlib.sha256(self.ledger.database.read_bytes()).hexdigest()
        bundle = inspect_review_bundle(
            database=self.ledger.database, job_id=self.job,
            job_dir=self.job_dir, normalized_audio=self.audio,
        )
        self.assertEqual(hashlib.sha256(self.ledger.database.read_bytes()).hexdigest(), before)
        self.assertEqual(bundle["whole_file_qa"], "UNVERIFIED")
        self.assertEqual(bundle["operator_listening_attestation"], "NOT_RECORDED")
        self.assertEqual(bundle["previous_review_evidence_sha256"], hold["evidence_sha256"])
        self.assertEqual(len(bundle["candidate_snapshot"]), 1)
        bundle["review_decisions"] = [{
            "candidate_index": 0, "status": "RESOLVED", "decision": "KEEP_CHIRP",
            "reviewed_audio_window_ms": [1900, 4100],
            "decision_reason": "Original audio confirms the Chirp wording.",
        }]
        spent = self.ledger.status(self.job)["spent_cents"]
        result = apply_review_bundle(
            database=self.ledger.database, job_id=self.job,
            job_dir=self.job_dir, normalized_audio=self.audio, bundle=bundle,
            reviewer="local-reviewer", review_reference="listen-session-02",
            confirm_listened=True,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["operator_listening_attestation"], "SELF_ATTESTED")
        self.assertFalse(result["provider_calls_started"])
        self.assertFalse(result["drive_mutation_started"])
        self.assertEqual(self.ledger.status(self.job)["spent_cents"], spent)

    def test_review_bundle_rejects_candidate_tamper_and_stale_revision(self):
        hold = complete_adjudication_or_hold(
            ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
            reviewer="local-reviewer", review_reference="listen-session-01",
        )
        bundle = inspect_review_bundle(
            database=self.ledger.database, job_id=self.job,
            job_dir=self.job_dir, normalized_audio=self.audio,
        )
        bundle["candidate_snapshot"][0]["evidence"]["gemini_normalized_excerpt"] = "tampered"
        kwargs = {
            "database": self.ledger.database, "job_id": self.job,
            "job_dir": self.job_dir, "normalized_audio": self.audio,
            "bundle": bundle, "reviewer": "local-reviewer",
            "review_reference": "listen-session-02", "confirm_listened": True,
        }
        with self.assertRaisesRegex(V2Conflict, "candidate_snapshot"):
            apply_review_bundle(**kwargs)

        current = self.ledger.status(self.job)
        self.ledger.resume_adjudication_review(
            self.job, revision=current["revision"], reviewer="local-reviewer",
            review_reference="resume-review", previous_evidence_sha=hold["evidence_sha256"],
        )
        clean_bundle = inspect_review_bundle(
            database=self.ledger.database, job_id=self.job,
            job_dir=self.job_dir, normalized_audio=self.audio,
        )
        self.assertNotEqual(clean_bundle["revision"], bundle["revision"])
        with self.assertRaisesRegex(V2Conflict, "revision"):
            apply_review_bundle(**{**kwargs, "bundle": bundle})

    def test_review_bundle_correction_is_bound_to_exact_chirp_words(self):
        complete_adjudication_or_hold(
            ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
            reviewer="local-reviewer", review_reference="listen-session-01",
        )
        bundle = inspect_review_bundle(
            database=self.ledger.database, job_id=self.job,
            job_dir=self.job_dir, normalized_audio=self.audio,
        )
        bundle["review_decisions"] = [{
            "candidate_index": 0, "status": "RESOLVED", "decision": "CORRECT_CHIRP",
            "reviewed_audio_window_ms": [1900, 4100],
            "decision_reason": "The original audio clearly says 復此.",
        }]
        bundle["replacements"] = [{
            "candidate_index": 0, "start_word": 0, "end_word": 1,
            "corrected_text": "復此",
            "chirp_word_evidence_shas": [_word_evidence(self.words[0], 0, self.sha)],
        }]
        result = apply_review_bundle(
            database=self.ledger.database, job_id=self.job,
            job_dir=self.job_dir, normalized_audio=self.audio, bundle=bundle,
            reviewer="local-reviewer", review_reference="listen-session-02",
            confirm_listened=True,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(len(result["replacements"]), 1)
        qa = complete_evidence_bound_qa(
            ledger=GoldenV2Ledger.open_existing(self.ledger.database),
            claim=self.ledger.claim_next(self.job, worker_id="local-qa"),
            job_dir=self.job_dir,
        )
        self.assertEqual(qa["status"], "PASS")
        self.assertIn("復此", (self.v2dir / "final.srt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
