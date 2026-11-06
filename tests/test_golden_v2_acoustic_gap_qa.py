"""Deterministic acoustic gap QA classification, text QA, and tier preservation tests.

SCOPE & SYSTEM BOUNDARY:
- COMPLETED: Deterministic offline QA classification rules for long audio gaps
  (SPEECH_CONFIRMED, NONSPEECH_CONFIRMED, REVIEW_REQUIRED), strict rejection of
  unearned Golden PASS based on text reference matching, and isolation of the
  AUTO_ACCEPTED_WITH_REVIEW machine closure tier from Golden PASS and AUTO_DELIVERED.
- UNFINISHED / SEPARATE TRACK: This test suite uses synthetic audio bytes and triage
  sidecars. It does NOT implement live ASR speech recovery, sound-gap remediation,
  or missing word reconstruction for Canary 250503 (e.g. 43:20.160–44:30.080 and
  2:21:39.320–2:23:08.360) or 20221217. Actual live speech-gap remediation remains
  separately open under Issue #160 (Gates G2/G8).
"""
import hashlib
import json
import sqlite3
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
        """REVIEW_REQUIRED disposition is recognized as advisory review, retaining original evidence."""
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

        # Preserves REVIEW_REQUIRED disposition in acoustic review items
        self.assertEqual(result["acoustic_review_items"][0]["disposition"], "REVIEW_REQUIRED")
        report = render_human_review_advisory(result, lesson_title="測試長空窗")
        self.assertIn("REVIEW_REQUIRED；仍待音訊核對", report)

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

    def test_prepare_limited_manifest_preserves_review_required_provenance(self):
        """prepare_limited_manifest retains REVIEW_REQUIRED without destructive mutation."""
        from app.pipeline.golden_v2_ai_limited_closeout import prepare_limited_manifest
        from app.jobs.golden_v2_ledger import STAGES
        job_id = "v2p_" + "b" * 32
        job_dir = self.root / "jobs" / job_id
        job_dir.mkdir(parents=True)
        assets = job_dir / "golden-v2"
        assets.mkdir()
        (job_dir / "normalized.flac").write_bytes(self.audio_bytes)
        (job_dir / "source-preflight.json").write_text(json.dumps({
            "source_sha256": self.source_sha,
            "drive_observation": {"file_id": "f999", "version": "12",
                                  "size_bytes": len(self.audio_bytes), "downloaded_md5": "unit-md5"},
            "drive_source_path": "gdrive:lecture/source.wav",
        }))
        srt_file = assets / "review.srt"
        srt_file.write_text("1\n00:00:00,000 --> 00:00:01,000\n佛法\n\n"
                            "2\n00:00:01,100 --> 00:00:02,000\n修行\n")
        txt_file = assets / "review.txt"
        txt_file.write_text("佛法\n修行\n")

        triage_dict = {
            "job_id": job_id,
            "srt_sha256": hashlib.sha256(srt_file.read_bytes()).hexdigest(),
            "total_acoustic_gaps": 2,
            "all_acoustic_gaps": [
                {"start_ms": 2000, "end_ms": 5000, "acoustic_disposition": "REVIEW_REQUIRED",
                 "classification": "NO_SRT_TEXT_LONG_GAP"},
                {"start_ms": 5000, "end_ms": 7000, "acoustic_disposition": "NONSPEECH_CONFIRMED",
                 "classification": "NO_SRT_TEXT_SHORT_GAP"},
            ],
        }
        triage_raw = json.dumps(triage_dict, indent=2).encode("utf-8")
        triage_file = assets / "triage.json"
        triage_file.write_bytes(triage_raw)

        # Set up stages in ledger
        ledger_path = self.root / "ledger.sqlite"
        ledger = GoldenV2Ledger(ledger_path)
        with sqlite3.connect(ledger_path) as db:
            db.execute(
                "INSERT INTO v2_jobs(id,source_sha,mode,revision,approval_reference,"
                "approved_cents,estimated_cents,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, self.source_sha, "FLASH_CHIRP", 3, "approved", 100, 100,
                 "NEEDS_REVIEW", "2026-10-25T00:00:00+00:00")
            )
            for ix, name in enumerate(STAGES):
                state = "REVIEW_REQUIRED" if name in ("chirp_independent", "gemini_primary") else "PENDING"
                db.execute("INSERT INTO v2_stages(job_id,ord,name,state,estimate_cents,"
                           "actual_cents,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (job_id, ix, name, state, 0, 0, "2026-10-25T00:00:00+00:00"))
            db.execute(
                "INSERT INTO v2_chirp_operations "
                "(job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,"
                "status,operation_name,request_id,output_uri,estimated_cents,"
                "purpose,result_sha,actual_cents,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "win0", self.source_sha, "b" * 64, "gs://test/audio", 0, 3000,
                 "DONE", "operation-123", "req01", "gs://test/out", 1,
                 "BASE", "c" * 64, 1, "2026-10-25T00:00:00+00:00")
            )

        manifest = prepare_limited_manifest(
            root=self.root,
            job_id=job_id,
            srt_path=srt_file,
            txt_path=txt_file,
            triage_path=triage_file,
            remote_names=("review_AI校對_含待複核.srt", "review_AI校對_含待複核.txt"),
        )

        # 1. Triage SHA256 matches untouched raw triage bytes
        self.assertEqual(manifest["triage_sha256"], hashlib.sha256(triage_raw).hexdigest())

        # 2. Classification counts record REVIEW_REQUIRED separately
        self.assertEqual(manifest["acoustic_classification_counts"], {
            "REVIEW_REQUIRED": 1,
            "NONSPEECH_CONFIRMED": 1,
        })

        # 3. open_items preserves the original REVIEW_REQUIRED disposition
        self.assertEqual(len(manifest["open_items"]), 1)
        self.assertEqual(manifest["open_items"][0]["disposition"], "REVIEW_REQUIRED")
        self.assertEqual(manifest["open_items"][0]["classification"], "NO_SRT_TEXT_LONG_GAP")
        self.assertEqual(manifest["open_items"][0]["start_ms"], 2000)
        self.assertEqual(manifest["open_items"][0]["end_ms"], 5000)

        # 4. Underlying triage file was NOT mutated
        self.assertEqual(triage_file.read_bytes(), triage_raw)


if __name__ == "__main__":
    unittest.main()
