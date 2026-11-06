"""Deterministic acoustic gap closure, text QA, and tier preservation tests.

Enforces Issue #169 work packages:
1. Long audio speech-without-subtitle intervals distinguishing SPEECH_CONFIRMED,
   NONSPEECH_CONFIRMED, and REVIEW_REQUIRED.
2. Text-only reference must NEVER count as acoustic truth or grant Golden PASS.
3. Strict preservation of AUTO_ACCEPTED_WITH_REVIEW distinct from Golden PASS
   and AUTO_DELIVERED.
4. Zero paid provider calls (offline synthetic audio and evidence only).
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_auto_closeout import prepare_auto_acceptance, _wire
from app.providers.golden_v2_auto_acceptance import (
    evaluate_auto_acceptance, render_human_review_advisory, SCHEMA, TIER,
)
from app.providers.golden_v2_auto_gap_triage import inspect_subtitle_gaps, load_bound_triage


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class DeterministicAcousticGapQATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job_id = "v2p_" + "a" * 32
        self.job_dir = self.root / "jobs" / self.job_id
        self.job_dir.mkdir(parents=True)
        (self.job_dir / "golden-v2").mkdir()

        # Offline synthetic normalized audio (120 seconds duration)
        self.audio_bytes = b"synthetic-120s-offline-flac-source-evidence"
        self.source_sha = _sha(self.audio_bytes)
        (self.job_dir / "normalized.flac").write_bytes(self.audio_bytes)

        # Base subtitles with a 69.9s long interior gap (20.0s to 89.9s)
        # simulating Canary 250503 long untranscribed intervals
        self.srt_bytes = (
            "1\n00:00:00,000 --> 00:00:20,000\n開場導論與引言。\n\n"
            "2\n00:01:29,900 --> 00:02:00,000\n總結與後續討論。\n\n"
        ).encode("utf-8")
        self.txt_bytes = "開場導論與引言。\n總結與後續討論。\n".encode("utf-8")
        self.duration_ms = 120_000

    def test_long_gap_speech_confirmed_requires_human_review(self):
        """Acoustically confirmed speech in a long gap stays open and alerts review."""
        # Gap 20,000 ms to 89,900 ms (69.9 seconds)
        gap_item = {
            "index": 0,
            "start_ms": 20_000,
            "end_ms": 89_900,
            "disposition": "SPEECH_CONFIRMED",
            "reason": "local_segmented_audio_evidence",
            "slice_count": 3,
            "probes": [
                {"start_ms": 20_000, "end_ms": 50_000, "disposition": "SPEECH_CONFIRMED",
                 "audible_above_rms_threshold": True, "max_volume_dbfs": -18.5,
                 "vad": {"robust_speech": True, "robust_non_speech": False}},
                {"start_ms": 50_000, "end_ms": 80_000, "disposition": "SPEECH_CONFIRMED",
                 "audible_above_rms_threshold": True, "max_volume_dbfs": -16.2,
                 "vad": {"robust_speech": True, "robust_non_speech": False}},
                {"start_ms": 80_000, "end_ms": 89_900, "disposition": "SPEECH_CONFIRMED",
                 "audible_above_rms_threshold": True, "max_volume_dbfs": -22.0,
                 "vad": {"robust_speech": True, "robust_non_speech": False}},
            ],
        }

        result = evaluate_auto_acceptance(
            job_id=self.job_id,
            source_sha256=self.source_sha,
            source_audio_sha_verified=True,
            source_duration_ms=self.duration_ms,
            srt_bytes=self.srt_bytes,
            txt_bytes=self.txt_bytes,
            reference_txt_bytes=None,
            original_srt_bytes=None,
            acoustic_review_items=[gap_item],
            chirp_stage="COMPLETE",
            external_provider_reconciled=True,
        )

        # Must close under machine tier, but MUST flag human review advisory
        self.assertEqual(result["status"], TIER)
        self.assertTrue(result["can_close_without_human"])
        self.assertFalse(result["formal_golden_pass"])
        self.assertEqual(result["golden_qa_status"], "NOT_CLAIMED")
        self.assertEqual(result["acoustic_nonspeech_confirmed_count"], 0)
        self.assertEqual(result["acoustic_open_item_count"], 1)
        self.assertTrue(result["human_review_advisory"]["recommended"])

        # Render report confirms explicit speech disposition
        report = render_human_review_advisory(result, lesson_title="測試長空窗")
        self.assertIn("SPEECH_CONFIRMED；仍待音訊核對", report)
        self.assertIn("00:00:20.000–00:01:29.900", report)

    def test_long_gap_nonspeech_confirmed_dismisses_gap_from_advisory(self):
        """Contiguous verified silence dismisses the advisory for that gap."""
        gap_item = {
            "index": 0,
            "start_ms": 20_000,
            "end_ms": 89_900,
            "disposition": "NONSPEECH_CONFIRMED",
            "reason": "local_segmented_audio_evidence",
            "slice_count": 3,
            "probes": [
                {"start_ms": 20_000, "end_ms": 50_000, "disposition": "NONSPEECH_CONFIRMED",
                 "max_volume_dbfs": -75.0, "reason": "local_segmented_audio_evidence"},
                {"start_ms": 50_000, "end_ms": 80_000, "disposition": "NONSPEECH_CONFIRMED",
                 "max_volume_dbfs": -80.0, "reason": "local_segmented_audio_evidence"},
                {"start_ms": 80_000, "end_ms": 89_900, "disposition": "NONSPEECH_CONFIRMED",
                 "max_volume_dbfs": -72.0, "reason": "local_segmented_audio_evidence"},
            ],
        }

        result = evaluate_auto_acceptance(
            job_id=self.job_id,
            source_sha256=self.source_sha,
            source_audio_sha_verified=True,
            source_duration_ms=self.duration_ms,
            srt_bytes=self.srt_bytes,
            txt_bytes=self.txt_bytes,
            reference_txt_bytes=None,
            original_srt_bytes=None,
            acoustic_review_items=[gap_item],
            chirp_stage="COMPLETE",
            external_provider_reconciled=True,
        )

        self.assertEqual(result["status"], TIER)
        self.assertTrue(result["can_close_without_human"])
        self.assertEqual(result["acoustic_nonspeech_confirmed_count"], 1)
        self.assertEqual(result["acoustic_open_item_count"], 0)
        # No unresolved items remain
        self.assertFalse(result["human_review_advisory"]["recommended"])
        # Crucial: non-speech silence NEVER promotes to Golden PASS
        self.assertFalse(result["formal_golden_pass"])

    def test_long_gap_review_required_recognized_without_technical_blocker(self):
        """REVIEW_REQUIRED disposition is recognized as advisory review, not a fatal crash."""
        gap_item = {
            "index": 0,
            "start_ms": 20_000,
            "end_ms": 89_900,
            "disposition": "REVIEW_REQUIRED",
            "acoustic_disposition": "REVIEW_REQUIRED",
        }

        result = evaluate_auto_acceptance(
            job_id=self.job_id,
            source_sha256=self.source_sha,
            source_audio_sha_verified=True,
            source_duration_ms=self.duration_ms,
            srt_bytes=self.srt_bytes,
            txt_bytes=self.txt_bytes,
            reference_txt_bytes=None,
            original_srt_bytes=None,
            acoustic_review_items=[gap_item],
            chirp_stage="COMPLETE",
            external_provider_reconciled=True,
        )

        # Must NOT be blocked by invalid_acoustic_disposition
        self.assertNotIn("invalid_acoustic_disposition", result["technical_blockers"])
        self.assertEqual(result["status"], TIER)
        self.assertTrue(result["can_close_without_human"])
        self.assertTrue(result["human_review_advisory"]["recommended"])
        self.assertEqual(result["acoustic_open_item_count"], 1)
        self.assertFalse(result["formal_golden_pass"])

    def test_text_only_reference_never_counts_as_acoustic_truth(self):
        """Text reference matching must never dismiss speech or certify completeness."""
        # Acoustic probe says SPEECH_CONFIRMED (speaker was speaking)
        gap_item = {
            "index": 0,
            "start_ms": 20_000,
            "end_ms": 89_900,
            "disposition": "SPEECH_CONFIRMED",
        }

        # Reference TXT has text that partially or completely matches
        # e.g. "心不隨境轉，境隨心轉。"
        reference_txt = (
            "開場導論與引言。\n"
            "心不隨境轉，念頭清淨方是正道。\n"
            "總結與後續討論。\n"
        ).encode("utf-8")

        result = evaluate_auto_acceptance(
            job_id=self.job_id,
            source_sha256=self.source_sha,
            source_audio_sha_verified=True,
            source_duration_ms=self.duration_ms,
            srt_bytes=self.srt_bytes,
            txt_bytes=self.txt_bytes,
            reference_txt_bytes=reference_txt,
            original_srt_bytes=None,
            acoustic_review_items=[gap_item],
            chirp_stage="COMPLETE",
            external_provider_reconciled=True,
        )

        # 1. Similarity is computed for context only
        ref_match = result["reference_txt_comparison"]
        self.assertIsNotNone(ref_match)
        self.assertFalse(ref_match["accuracy_certification"])

        # 2. Text reference must NOT dismiss the SPEECH_CONFIRMED gap
        self.assertEqual(result["acoustic_nonspeech_confirmed_count"], 0)
        self.assertEqual(result["acoustic_open_item_count"], 1)
        self.assertTrue(result["human_review_advisory"]["recommended"])

        # 3. Golden PASS must NOT be granted based on text match
        self.assertFalse(result["formal_golden_pass"])
        self.assertEqual(result["golden_qa_status"], "NOT_CLAIMED")

    def test_text_reference_presence_does_not_override_acoustic_silence(self):
        """Blackboard notes in reference TXT do not invalidate acoustic NONSPEECH proof."""
        gap_item = {
            "index": 0,
            "start_ms": 20_000,
            "end_ms": 89_900,
            "disposition": "NONSPEECH_CONFIRMED",
            "reason": "local_segmented_audio_evidence",
            "slice_count": 1,
            "probes": [{
                "start_ms": 20_000, "end_ms": 89_900, "disposition": "NONSPEECH_CONFIRMED",
                "max_volume_dbfs": -82.0, "reason": "local_segmented_audio_evidence",
            }],
        }

        # Reference TXT has notes during blackboard pause
        ref_txt = (
            "開場導論與引言。\n"
            "（講師板書：華嚴最初三七日，阿含十二方等八）\n"
            "總結與後續討論。\n"
        ).encode("utf-8")

        result = evaluate_auto_acceptance(
            job_id=self.job_id,
            source_sha256=self.source_sha,
            source_audio_sha_verified=True,
            source_duration_ms=self.duration_ms,
            srt_bytes=self.srt_bytes,
            txt_bytes=self.txt_bytes,
            reference_txt_bytes=ref_txt,
            original_srt_bytes=None,
            acoustic_review_items=[gap_item],
            chirp_stage="COMPLETE",
            external_provider_reconciled=True,
        )

        # Acoustic truth is preserved: non-speech confirmed
        self.assertEqual(result["acoustic_nonspeech_confirmed_count"], 1)
        # Reference differences are disclosed contextually, not hallucinated
        self.assertIn("reference_txt_comparison", result)
        self.assertFalse(result["formal_golden_pass"])

    def test_auto_accepted_with_review_distinct_from_golden_pass_and_delivered(self):
        """AUTO_ACCEPTED_WITH_REVIEW is isolated in ledger and does not grant Golden PASS."""
        ledger = GoldenV2Ledger(self.root / "ledger.sqlite")
        job = ledger.create_from_preflight(
            source_sha=self.source_sha,
            mode="FLASH_CHIRP",
            estimated_stage_cents=dict(gemini_primary=10, chirp_independent=10,
                                       compare=0, third_optional=0, adjudication=0, golden_qa=0),
            registration_sha256="b" * 64,
        )
        ledger.approve(job, revision=0, approved_cents=20, reference="test-ref", actor="owner")
        ledger.claim_next(job, worker_id="test-worker")

        job_dir = self.root / "jobs" / job
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "normalized.flac").write_bytes(self.audio_bytes)
        (job_dir / "golden-v2").mkdir(parents=True, exist_ok=True)
        (job_dir / "source-preflight.json").write_text(json.dumps({
            "source_sha256": self.source_sha,
            "drive_observation": {"file_id": "source-id", "version": "7", "parents": ["parent"]},
            "drive_source_path": "test/path/source.mp3",
        }))

        gap_item = {
            "index": 0,
            "start_ms": 20_000,
            "end_ms": 89_900,
            "disposition": "REVIEW",
        }

        eval_result = evaluate_auto_acceptance(
            job_id=job,
            source_sha256=self.source_sha,
            source_audio_sha_verified=True,
            source_duration_ms=self.duration_ms,
            srt_bytes=self.srt_bytes,
            txt_bytes=self.txt_bytes,
            reference_txt_bytes=None,
            original_srt_bytes=None,
            acoustic_review_items=[gap_item],
            chirp_stage="COMPLETE",
            external_provider_reconciled=True,
        )

        srt_file = job_dir / "test_自動驗收_附複核建議.srt"
        txt_file = job_dir / "test_自動驗收_附複核建議.txt"
        srt_file.write_bytes(self.srt_bytes)
        txt_file.write_bytes(self.txt_bytes)

        prepared = prepare_auto_acceptance(
            root=self.root,
            job_id=job,
            result=eval_result,
            srt_path=srt_file,
            txt_path=txt_file,
            target_names=("test_自動驗收_附複核建議.srt", "test_自動驗收_附複核建議.txt"),
            lesson_title="音訊結案測試",
        )

        # 1. State in independent table is PREPARED, NOT AUTO_DELIVERED
        self.assertEqual(prepared["state"], "PREPARED")

        # 2. Main ledger stages are NOT modified to Golden PASS
        status = ledger.status(job)
        qa_stage = next(s for s in status["stages"] if s["name"] == "golden_qa")
        self.assertNotEqual(qa_stage["state"], "COMPLETE")
        self.assertNotEqual(status["status"], "DELIVERED")

    def test_load_bound_triage_handles_review_required(self):
        """load_bound_triage accepts both REVIEW and REVIEW_REQUIRED dispositions."""
        triage_report = {
            "schema": "golden-v2-auto-gap-triage-v1",
            "source_sha256": self.source_sha,
            "srt_sha256": _sha(self.srt_bytes),
            "duration_ms": self.duration_ms,
            "gap_min_ms": 3000,
            "total_gaps": 1,
            "gaps": [{
                "index": 0,
                "start_ms": 20_000,
                "end_ms": 89_900,
                "disposition": "REVIEW_REQUIRED",
                "reason": "triage_slice_budget_exceeded",
                "slice_count": 0,
                "probes": [],
            }],
            "nonspeech_confirmed_count": 0,
            "speech_confirmed_count": 0,
            "review_count": 1,
            "no_paid_provider_calls": True,
            "human_listening_attested": False,
            "golden_qa_pass": False,
        }

        digest = _sha(self.srt_bytes)
        path = self.job_dir / "golden-v2" / f"auto-gap-triage-{digest[:20]}.json"
        path.write_bytes(_wire(triage_report))

        loaded = load_bound_triage(
            job_dir=self.job_dir,
            source_sha256=self.source_sha,
            srt_bytes=self.srt_bytes,
            duration_ms=self.duration_ms,
        )

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["total_gaps"], 1)
        self.assertEqual(loaded["review_count"], 1)
        self.assertFalse(loaded["golden_qa_pass"])


if __name__ == "__main__":
    unittest.main()
