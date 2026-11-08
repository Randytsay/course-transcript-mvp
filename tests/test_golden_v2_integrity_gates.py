"""Tests for the three Golden V2 integrity gates (Phase A).

RED-first. These encode the audit findings as executable gates:

1. ALIGNMENT HEALTH GATE - a comparison whose reference text is truncated or
   wildly misaligned must be REFUSED before it can create thousands of
   adjudicated candidates. Signature from the real 261017 incident: a large
   constant character offset (>= 500) across >50% of candidates.
2. REVIEW EVIDENCE GATE - an adjudication batch may not be a rubber stamp:
   a single decision_reason covering every candidate, or mechanically derived
   [start-1000, end+1000] review windows, or fabricated windows attached to
   untimed candidates, must be REFUSED.
3. PRIMARY REPAIR LEDGER CAS - a primary-stage repair must be recorded in the
   ledger with a compare-and-swap on the previous evidence SHA, so an
   in-place file mutation cannot masquerade as a reviewed artifact.
"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_alignment_health import (
    AlignmentHealthError, assess_alignment_health,
)
from app.pipeline.golden_v2_review_evidence import (
    ReviewEvidenceError, assess_review_evidence,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _candidates(count: int, *, offset: int = 0, untimed: int = 0) -> list[dict]:
    """Build comparison candidates with a deliberate char-offset signature."""
    out = []
    for i in range(count):
        start = None if i < untimed else 1000 + i * 100
        end = None if i < untimed else 1050 + i * 100
        out.append({
            "kind": "TEXT_DISAGREEMENT",
            "chirp_char_range": [offset + i * 10, offset + i * 10 + 8],
            "gemini_char_range": [i * 10, i * 10 + 8],
            "source_time_start": start,
            "source_time_end": end,
            "chirp_normalized_excerpt": "候選文字",
            "gemini_normalized_excerpt": "參考文字",
        })
    return out


class AlignmentHealthGateTests(unittest.TestCase):
    def test_healthy_alignment_passes(self):
        report = assess_alignment_health(
            candidates=_candidates(40), comparison=None,
            primary_coverage_ratio=1.0, duration_ms=10000)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["large_offset_candidate_count"], 0)

    def test_constant_large_offset_is_refused(self):
        """The real 261017 signature: 65.7% of candidates carry |delta| >= 500."""
        report = assess_alignment_health(
            candidates=_candidates(200, offset=5200), comparison=None,
            primary_coverage_ratio=1.0, duration_ms=10781820)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertGreaterEqual(report["large_offset_candidate_count"], 100)
        self.assertIn("systematic_reference_offset", report["reasons"])

    def test_incomplete_primary_coverage_is_refused(self):
        """251025/251018: only 20-30% of the audio had a second engine."""
        report = assess_alignment_health(
            candidates=_candidates(400), comparison=None,
            primary_coverage_ratio=0.204, duration_ms=13254075)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("incomplete_primary_coverage", report["reasons"])

    def test_small_scattered_offsets_are_not_blocked(self):
        """A handful of disagreements must not be mistaken for misalignment."""
        cands = _candidates(400)
        for c in cands[:5]:
            c["gemini_char_range"] = [c["chirp_char_range"][0] + 900,
                                      c["chirp_char_range"][1] + 900]
        report = assess_alignment_health(
            candidates=cands, comparison=None,
            primary_coverage_ratio=1.0, duration_ms=10000)
        self.assertEqual(report["status"], "PASS")


class ReviewEvidenceGateTests(unittest.TestCase):
    def _decision(self, index, *, reason, window, decision="KEEP_CHIRP"):
        return {
            "candidate_index": index, "status": "RESOLVED",
            "decision": decision, "decision_reason": reason,
            "reviewed_audio_window_ms": window,
            "audio_evidence_checked": True,
        }

    def test_single_reason_rubber_stamp_is_refused(self):
        """Real signature: 3166 decisions, 1 distinct reason."""
        cands = _candidates(300)
        decisions = [
            self._decision(i, reason="保留 Chirp 原文與聲學語音",
                           window=[c["source_time_start"] - 1000,
                                   c["source_time_end"] + 1000])
            for i, c in enumerate(cands)
        ]
        with self.assertRaises(ReviewEvidenceError) as ctx:
            assess_review_evidence(candidates=cands, decisions=decisions)
        self.assertIn("uniform_decision_reason", str(ctx.exception))

    def test_mechanical_review_windows_are_refused(self):
        """Real signature: 100% of timed windows were [start-1000, end+1000]."""
        cands = _candidates(30)
        decisions = [
            self._decision(i, reason=f"逐筆複核第 {i} 筆，聽到原音",
                           window=[c["source_time_start"] - 1000,
                                   c["source_time_end"] + 1000])
            for i, c in enumerate(cands)
        ]
        with self.assertRaises(ReviewEvidenceError) as ctx:
            assess_review_evidence(candidates=cands, decisions=decisions)
        self.assertIn("mechanical_review_window", str(ctx.exception))

    def test_fabricated_window_on_untimed_candidate_is_refused(self):
        """A window on an untimed candidate must at least bracket the
        adjacent Chirp words; otherwise it refers to no known audio."""
        cands = _candidates(20, untimed=5)
        for c in cands:
            if c["source_time_start"] is None:
                c["adjacent_chirp_context"] = {
                    "left_chirp_word_ms": 5000, "right_chirp_word_ms": 5200}
        decisions = []
        for i, c in enumerate(cands):
            if c["source_time_start"] is None:
                window = [0, 5000]      # does NOT bracket the adjacent words
            else:
                window = [c["source_time_start"] - 700,
                          c["source_time_end"] + 700]
            decisions.append(self._decision(
                i, reason=f"第 {i} 筆個別理由說明內容",
                window=window))
        with self.assertRaises(ReviewEvidenceError) as ctx:
            assess_review_evidence(candidates=cands, decisions=decisions)
        self.assertIn("unanchored_review_window", str(ctx.exception))

    def test_untimed_candidate_anchored_to_adjacent_context_is_accepted(self):
        """The real 519 untimed decisions were anchored to adjacent context.
        That anchoring alone is legitimate; what was illegitimate was that
        every window came from one formula."""
        cands = _candidates(20, untimed=5)
        for c in cands:
            if c["source_time_start"] is None:
                c["adjacent_chirp_context"] = {
                    "left_chirp_word_ms": 5000 + c["chirp_char_range"][0] * 3,
                    "right_chirp_word_ms": 5200 + c["chirp_char_range"][0] * 3}
        decisions = []
        for i, c in enumerate(cands):
            if c["source_time_start"] is None:
                ctx = c["adjacent_chirp_context"]
                window = [ctx["left_chirp_word_ms"] - 130 + i * 7,
                          ctx["right_chirp_word_ms"] + 210 + i * 11]
            else:
                window = [c["source_time_start"] - 700 + i * 3,
                          c["source_time_end"] + 700 + i * 5]
            decisions.append(self._decision(
                i, reason=f"第 {i} 筆：聽到原音後個別說明",
                window=window))
        report = assess_review_evidence(candidates=cands, decisions=decisions)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["untimed_decision_count"], 5)

    def test_real_production_batch_signature_is_refused(self):
        """The audited 261017 batch: 3,166 decisions, one reason, and every
        window equal to [anchor-1000, anchor+1000]."""
        cands = _candidates(60, untimed=12)
        for c in cands:
            if c["source_time_start"] is None:
                c["adjacent_chirp_context"] = {
                    "left_chirp_word_ms": 100, "right_chirp_word_ms": 200}
        decisions = []
        for i, c in enumerate(cands):
            if c["source_time_start"] is not None:
                anchor = (c["source_time_start"], c["source_time_end"])
            else:
                ctx = c["adjacent_chirp_context"]
                anchor = (ctx["left_chirp_word_ms"], ctx["right_chirp_word_ms"])
            decisions.append(self._decision(
                i, reason="Retain verbatim Chirp timing and acoustic speech",
                window=[anchor[0] - 1000, anchor[1] + 1000]))
        with self.assertRaises(ReviewEvidenceError) as ctx:
            assess_review_evidence(candidates=cands, decisions=decisions)
        message = str(ctx.exception)
        self.assertIn("uniform_decision_reason", message)
        self.assertIn("mechanical_review_window", message)

    def test_genuine_individual_review_passes(self):
        cands = _candidates(12)
        decisions = [
            self._decision(
                i, reason=f"第 {i} 筆：聽到『候選文字』與原音一致，保留",
                window=[c["source_time_start"] - 137 + i * 13,
                        c["source_time_end"] + 91 + i * 17])
            for i, c in enumerate(cands)
        ]
        report = assess_review_evidence(candidates=cands, decisions=decisions)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["distinct_decision_reasons"], 12)


class PrimaryRepairLedgerCastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ledger.sqlite"
        self.ledger = GoldenV2Ledger(self.db)
        self.job_id = self.ledger.create_idempotent(
            source_sha=_sha("audio"), mode="QUALITY_3WAY",
            estimated_stage_cents={
                "gemini_primary": 100, "chirp_independent": 100,
                "compare": 0, "third_optional": 100,
                "adjudication": 0, "golden_qa": 0,
            })

    def _approve(self):
        job = self.ledger.status(self.job_id)
        self.ledger.approve(
            self.job_id, revision=job["revision"], approved_cents=500,
            reference="owner-approval-ref", actor="verified-human")

    def _complete_primary(self, evidence_sha: str):
        """Drive gemini_primary to COMPLETE carrying the given evidence SHA."""
        claim = self.ledger.claim_next(self.job_id, worker_id="offline")
        self.assertEqual(claim["stage"], "gemini_primary")
        self.ledger.complete(claim, evidence_sha=evidence_sha, actual_cents=0)

    def test_primary_repair_requires_matching_previous_evidence(self):
        self._approve()
        self._complete_primary("a" * 64)
        job = self.ledger.status(self.job_id)
        with self.assertRaises(V2Conflict):
            self.ledger.record_primary_repair(
                self.job_id, revision=job["revision"],
                previous_evidence_sha="c" * 64,
                repaired_evidence_sha="b" * 64,
                reviewer="verified-human",
                review_reference="local-listening-01")

    def test_primary_repair_is_recorded_and_audited(self):
        self._approve()
        self._complete_primary("a" * 64)
        job = self.ledger.status(self.job_id)
        self.ledger.record_primary_repair(
            self.job_id, revision=job["revision"],
            previous_evidence_sha="a" * 64,
            repaired_evidence_sha="b" * 64,
            reviewer="verified-human",
            review_reference="local-listening-01")
        rows = self.ledger.primary_repairs(self.job_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["previous_evidence_sha"], "a" * 64)
        self.assertEqual(rows[0]["repaired_evidence_sha"], "b" * 64)
        self.assertEqual(rows[0]["reviewer"], "verified-human")
        # the stage now points at the repaired artifact
        stages = self.ledger.status(self.job_id)["stages"]
        self.assertEqual(stages[0]["evidence_sha"], "b" * 64)

    def test_stale_primary_repair_is_rejected(self):
        """CAS: a repair quoting a superseded evidence SHA must be refused."""
        self._approve()
        self._complete_primary("a" * 64)
        job = self.ledger.status(self.job_id)
        self.ledger.record_primary_repair(
            self.job_id, revision=job["revision"],
            previous_evidence_sha="a" * 64,
            repaired_evidence_sha="b" * 64,
            reviewer="verified-human",
            review_reference="local-listening-01")
        job = self.ledger.status(self.job_id)
        with self.assertRaises(V2Conflict):
            self.ledger.record_primary_repair(
                self.job_id, revision=job["revision"],
                previous_evidence_sha="a" * 64,
                repaired_evidence_sha="c" * 64,
                reviewer="verified-human",
                review_reference="local-listening-01")


if __name__ == "__main__":
    unittest.main()
