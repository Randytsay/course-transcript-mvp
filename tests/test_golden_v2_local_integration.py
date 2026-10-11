"""Integration proof uses immutable mock artifacts, no provider or Drive side effects."""
import unittest

from app.jobs.golden_v2_stage_contract import V2Selection
from app.pipeline.golden_v2_dry_run import preview_golden_v2

SHA = "f" * 64
WORDS = [
    {"word": "復次", "start_ms": 0, "end_ms": 500},
    {"word": "文殊師利", "start_ms": 500, "end_ms": 1700},
    {"word": "男身具七寶", "start_ms": 1700, "end_ms": 3300},
]
CUES = [
    {"segment_id": "s1", "start_ms": 0, "end_ms": 1700,
     "timing_source": "chirp_3", "corrected_text": "復次文殊師利。",
     "acoustic_verified": True, "adjudication": "APPROVED"},
    {"segment_id": "s2", "start_ms": 1700, "end_ms": 3300,
     "timing_source": "chirp_3", "corrected_text": "男身具七寶。",
     "acoustic_verified": True, "adjudication": "APPROVED"},
]
PRIMARY = {
    "model": "gemini-3.8-flash", "source_sha256": SHA,
    "primary_candidate_complete": True, "text": "復次文殊師利。男身具七寶。",
}


class GoldenV2LocalIntegrationTests(unittest.TestCase):
    def test_three_engine_mock_preview_never_calls_external_services(self):
        result = preview_golden_v2(
            V2Selection(SHA), gemini_primary=PRIMARY,
            chirp_words=WORDS, candidate_cues=CUES,
            transcribe_text="復次文殊師利，男身具七寶。",
        )
        self.assertEqual(result["status"], "STRUCTURAL_PASS_ONLY")
        self.assertFalse(result["golden_pass"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["drive_mutations"], 0)
        self.assertIn("復次文殊師利。\n男身具七寶。\n", result["outputs"]["transcript_txt"])
        self.assertEqual(result["outputs"]["cue_count"], 2)

    def test_missing_third_engine_in_quality_mode_blocks(self):
        result = preview_golden_v2(
            V2Selection(SHA), gemini_primary=PRIMARY,
            chirp_words=WORDS, candidate_cues=CUES,
        )
        self.assertEqual(result["reason"], "third_engine_missing")

    def test_disagreement_blocks_unreviewed_export(self):
        altered = {**PRIMARY, "text": "復次文殊師利。男生具有七寶。"}
        result = preview_golden_v2(
            V2Selection(SHA, mode="GEMINI_CHIRP"),
            gemini_primary=altered, chirp_words=WORDS, candidate_cues=CUES,
        )
        self.assertEqual(result["reason"], "engine_disagreements")
        self.assertNotIn("outputs", result)

    def test_incomplete_primary_never_starts_chirp_stage(self):
        result = preview_golden_v2(
            V2Selection(SHA), gemini_primary={**PRIMARY, "primary_candidate_complete": False},
            chirp_words=WORDS, candidate_cues=CUES,
            transcribe_text="復次文殊師利。男身具七寶。",
        )
        self.assertEqual(result["reason"], "gemini_primary_incomplete")
        self.assertNotIn("outputs", result)

    def test_unverified_cue_fails_closed_even_when_text_matches(self):
        bad = [dict(x) for x in CUES]
        bad[0]["acoustic_verified"] = False
        result = preview_golden_v2(
            V2Selection(SHA, mode="GEMINI_CHIRP"), gemini_primary=PRIMARY,
            chirp_words=WORDS, candidate_cues=bad,
        )
        self.assertEqual(result["reason"], "cue_evidence_incomplete")
        self.assertNotIn("outputs", result)

    def test_source_identity_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            preview_golden_v2(
                V2Selection("e" * 64), gemini_primary=PRIMARY,
                chirp_words=WORDS, candidate_cues=CUES,
                transcribe_text="復次文殊師利。男身具七寶。",
            )


if __name__ == "__main__":
    unittest.main()
