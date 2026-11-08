"""Golden V2 deterministic final gate tests (no audio provider calls)."""
import unittest

from app.providers.golden_v2_final_gate import validate_golden_v2


SRT = (
    "1\n00:00:00,100 --> 00:00:01,100\n復次文殊師利\n\n"
    "2\n00:00:01,100 --> 00:00:02,400\n男身具七寶。\n"
)
BOUNDARIES = {100, 1100, 2400}


def check(**overrides):
    args = {
        "srt": SRT, "transcript": "復次文殊師利\n男身具七寶。",
        "chirp_word_boundaries_ms": BOUNDARIES,
        "acoustic_gate": "PASS", "semantic_gate": "PASS",
        "comparison": {"candidates": []}, "adjudications": [],
    }
    args.update(overrides)
    return validate_golden_v2(**args)


class GoldenV2FinalGateTests(unittest.TestCase):
    def test_only_evidence_clean_document_may_pass(self):
        result = check()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["cue_count"], 2)
        self.assertTrue(result["safe_publish_allowed"])

    def test_fails_on_text_difference(self):
        result = check(transcript="復次文殊師利男生有七寶。")
        self.assertIn("srt_txt_mismatch", result["errors"])
        self.assertFalse(result["safe_publish_allowed"])

    def test_fails_on_invented_cue_boundary(self):
        result = check(chirp_word_boundaries_ms={100, 1100})
        self.assertIn("cue_without_chirp_word_boundary", result["errors"])

    def test_fails_when_acoustic_completeness_unverified(self):
        result = check(acoustic_gate="UNVERIFIED")
        self.assertIn("acoustic_completeness_not_passed", result["errors"])

    def test_candidate_must_have_audio_grounded_resolution(self):
        comparison = {"candidates": [{"kind": "TEXT_DISAGREEMENT"}]}
        result = check(comparison=comparison)
        self.assertIn("unresolved_comparison_candidate:0", result["errors"])
        result = check(
            comparison=comparison,
            adjudications=[{"candidate_index": 0, "status": "RESOLVED",
                            "audio_evidence_checked": True,
                            "decision_reason": "原音重聽與上下文一致"}],
        )
        self.assertEqual(result["status"], "PASS")

    def test_invalid_overlapping_srt_fails_closed(self):
        srt = SRT + "\n3\n00:00:02,000 --> 00:00:03,000\n錯誤\n"
        self.assertIn("invalid_srt", check(srt=srt)["errors"])


if __name__ == "__main__":
    unittest.main()
