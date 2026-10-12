"""Real local audio, SQLite claims, immutable Chirp timing, no provider billing."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import unittest
from unittest.mock import patch
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_stage_runner import (
    complete_comparison, complete_evidence_bound_qa,
)
from app.providers.golden_v2_acoustic_qa import inspect_v2_acoustic
from app.providers import golden_v2_acoustic_qa
from app.providers.golden_v2_audio_staging import file_sha256
from app.subtitles.golden_v2_render import _word_evidence, render_golden_v2


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


class GoldenV2LocalQATests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job_dir = self.root / "job"
        self.job_dir.mkdir()
        self.audio = self.job_dir / "normalized.flac"
        self._make_audio(audible_tail=False)
        self.sha = file_sha256(self.audio)
        self.store = GoldenV2Ledger(self.root / "ledger.sqlite")
        self.job = self.store.create(
            source_sha=self.sha, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 1, "chirp_independent": 1,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0,
            })
        self.store.approve(self.job, revision=0, approved_cents=2,
                           reference="LOCAL_FAKE_ONLY", actor="unit-test")
        self.root_v2 = self.job_dir / "golden-v2"
        self.root_v2.mkdir()
        primary = {"schema": "golden-v2-primary-stage-v1", "job_id": self.job,
                   "source_sha256": self.sha, "ready_for_chirp": True,
                   "assembled": {"text": "復次"}}
        primary_path = self.root_v2 / "primary-stage.json"
        primary_path.write_bytes(_wire(primary))
        self.store.complete(self.store.claim_next(self.job, worker_id="test"),
                            evidence_sha=hashlib.sha256(_wire(primary)).hexdigest(),
                            actual_cents=0)
        self.words = [{"word": "復次", "start_ms": 2050, "end_ms": 3900}]
        self.merged = {
            "schema": "golden-v2-chirp-merged-v1", "job_id": self.job,
            "source_sha": self.sha, "duration_ms": 8000,
            "words": self.words, "timing_authority": "chirp_3",
        }
        merged_path = self.root_v2 / "chirp-merged.json"
        merged_path.write_bytes(_wire(self.merged))
        self.store.complete(self.store.claim_next(self.job, worker_id="test"),
                            evidence_sha=file_sha256(merged_path), actual_cents=0)
        complete_comparison(
            ledger=self.store, claim=self.store.claim_next(self.job, worker_id="test"),
            job_dir=self.job_dir, primary_text="復次", chirp_words=self.words)
        self.store.complete(self.store.claim_next(self.job, worker_id="test"),
                            evidence_sha="b" * 64, actual_cents=0)
        self.qa_claim = self.store.claim_next(self.job, worker_id="test")

    def tearDown(self):
        self.temp.cleanup()

    def _make_audio(self, *, audible_tail):
        wav = self.root / "source.wav"
        with wave.open(str(wav), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16000)
            samples = bytearray()
            for sample in range(8 * 16000):
                second = sample / 16000
                audible = 2 <= second < 4 or (audible_tail and 6 <= second < 8)
                level = round(12000 * math.sin(2 * math.pi * 300 * second)) if audible else 0
                samples.extend(struct.pack("<h", level))
            target.writeframes(samples)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error",
                        "-i", str(wav), "-c:a", "flac", "-y", str(self.audio)],
                       check=True, timeout=30, capture_output=True)

    def test_silent_gaps_pass_with_verifiable_local_final_and_restart(self):
        report = complete_evidence_bound_qa(
            ledger=self.store, claim=self.qa_claim, job_dir=self.job_dir)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["acoustic_gate"], "PASS")
        self.assertTrue((self.root_v2 / "final.srt").is_file())
        self.assertTrue((self.root_v2 / "final-corrected-transcript.txt").is_file())
        self.assertEqual(GoldenV2Ledger(self.root / "ledger.sqlite").status(self.job)["status"],
                         "AWAITING_FINAL_REVIEW")
        self.assertFalse(report["drive_mutation_started"])

    def test_audible_untranscribed_tail_blocks_and_proposes_bounded_repair(self):
        self._make_audio(audible_tail=True)
        self.sha = file_sha256(self.audio)
        self.merged["source_sha"] = self.sha
        with self.assertRaises(V2Conflict):
            complete_evidence_bound_qa(
                ledger=self.store, claim=self.qa_claim, job_dir=self.job_dir)
        # Independently inspect verified alternate waveform without weakening ledger evidence.
        positive_speech = {
            "engine": "webrtcvad", "mode0_speech_ratio": 0.72,
            "mode3_speech_ratio": 0.44, "rms_dbfs": -18.0,
            "robust_non_speech": False, "robust_speech": True,
        }
        with patch.object(golden_v2_acoustic_qa, "_vad_assessment",
                          return_value=positive_speech):
            report = inspect_v2_acoustic(job_dir=self.job_dir, merged=self.merged)
        self.assertEqual(report["status"], "REVIEW")
        self.assertTrue(report["repair_plan"]["intervals"])
        self.assertFalse(report["repair_plan"]["submit_authorized"])
        self.assertEqual(report["gaps_examined"][-1]["position"], "TAIL")
        self.assertEqual(report["speech_confirmed_count"], 1)
        self.assertEqual(report["repair_plan"]["intervals"][0]["target_intervals"],
                         [{"start_ms": 3900, "end_ms": 8000, "gap_index": 0}])

    def test_audible_but_unclassified_gap_stays_manual_and_is_not_paid_candidate(self):
        self._make_audio(audible_tail=True)
        self.sha = file_sha256(self.audio)
        self.merged["source_sha"] = self.sha
        uncertain_vad = {
            "engine": "webrtcvad", "mode0_speech_ratio": 0.20,
            "mode3_speech_ratio": 0.04, "rms_dbfs": -24.0,
            "robust_non_speech": False, "robust_speech": False,
        }
        with patch.object(golden_v2_acoustic_qa, "_vad_assessment",
                          return_value=uncertain_vad):
            report = inspect_v2_acoustic(job_dir=self.job_dir, merged=self.merged)
        self.assertEqual(report["status"], "REVIEW")
        self.assertEqual(report["uncertain_count"], 1)
        self.assertEqual(report["repair_plan"]["intervals"], [])
        self.assertTrue(report["repair_plan"]["manual_review_required"])

    def test_large_gap_over_probe_cap_stays_review_without_full_rerun_proposal(self):
        self._make_audio(audible_tail=True)
        self.sha = file_sha256(self.audio)
        self.merged["source_sha"] = self.sha
        report = inspect_v2_acoustic(
            job_dir=self.job_dir, merged=self.merged, max_gap_probe_ms=3000)
        tail = report["gaps_examined"][-1]
        self.assertEqual(tail["reason"], "gap_exceeds_local_probe_cap")
        self.assertEqual(tail["disposition"], "REVIEW")
        self.assertIsNone(tail["vad"])
        self.assertEqual(report["repair_plan"]["intervals"], [])
        self.assertTrue(report["repair_plan"]["manual_review_required"])

    def test_gap_probe_count_is_bounded_and_uninspected_gaps_remain_blockers(self):
        report = inspect_v2_acoustic(
            job_dir=self.job_dir, merged=self.merged, max_gaps_to_probe=1)
        self.assertEqual(report["unexamined_gap_count"], 1)
        self.assertEqual(report["gaps_examined"][-1]["reason"],
                         "acoustic_gap_probe_limit_reached")
        self.assertEqual(report["status"], "REVIEW")

    def test_tampered_chirp_evidence_refuses_final(self):
        merged_path = self.root_v2 / "chirp-merged.json"
        self.merged["words"][0]["word"] = "偽造"
        merged_path.write_bytes(_wire(self.merged))
        with self.assertRaisesRegex(V2Conflict, "ledger/source evidence mismatch"):
            complete_evidence_bound_qa(
                ledger=self.store, claim=self.qa_claim, job_dir=self.job_dir)

    def test_forged_correction_and_invalid_timing_rejected(self):
        good = {"start_word": 0, "end_word": 1, "corrected_text": "復次。",
                "audio_evidence_checked": True, "source_sha256": self.sha,
                "chirp_word_evidence_shas": [
                    _word_evidence(self.words[0], 0, self.sha)],
                "decision_reason": "source checked"}
        out = render_golden_v2(merged=self.merged, replacements=[good])
        self.assertIn("復次。", out["srt"])
        self.assertIn("00:00:02,050 --> 00:00:03,900", out["srt"])
        bad = {**good, "chirp_word_evidence_shas": ["0" * 64]}
        with self.assertRaises(V2Conflict):
            render_golden_v2(merged=self.merged, replacements=[bad])
        with self.assertRaises(V2Conflict):
            render_golden_v2(merged={**self.merged, "words": [
                *self.words, {"word": "反序", "start_ms": 1000, "end_ms": 3000}]})
        with self.assertRaises(V2Conflict):
            complete_evidence_bound_qa(ledger=self.store, claim=self.qa_claim,
                                       job_dir=self.job_dir, replacements=[good])


if __name__ == "__main__":
    unittest.main()
