"""No-model final SRT/TXT consistency and acoustic guard tests."""
import unittest

from app.subtitles.golden_v2_finalizer import render_checked_candidates


def cue(segment_id, start, end, text):
    return {
        "segment_id": segment_id,
        "start_ms": start,
        "end_ms": end,
        "corrected_text": text,
        "timing_source": "chirp_3",
        "acoustic_verified": True,
        "adjudication": "APPROVED",
    }


class V2FinalizerTests(unittest.TestCase):
    def test_final_srt_and_txt_share_same_text(self):
        result = render_checked_candidates([
            cue("a", 0, 2500, "復次文殊師利。"),
            cue("b", 2500, 4000, "男身具七寶。"),
        ])
        self.assertEqual(result["transcript_txt"], "復次文殊師利。\n男身具七寶。\n")
        self.assertIn("00:00:02,500 --> 00:00:04,000", result["srt"])
        self.assertEqual(result["cue_count"], 2)
        self.assertFalse(result["golden_pass"])

    def test_invented_model_timing_is_refused(self):
        candidate = cue("a", 0, 1000, "測試")
        candidate["timing_source"] = "gemini-3.8-flash"
        with self.assertRaises(ValueError):
            render_checked_candidates([candidate])

    def test_unapproved_text_and_acoustic_gap_are_refused(self):
        sample = cue("a", 0, 1000, "測試")
        sample["adjudication"] = "NEEDS_AUDIO_REVIEW"
        with self.assertRaises(ValueError):
            render_checked_candidates([sample])
        sample["adjudication"] = "APPROVED"
        sample["acoustic_verified"] = False
        with self.assertRaises(ValueError):
            render_checked_candidates([sample])

    def test_overlaps_and_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            render_checked_candidates([
                cue("a", 0, 2000, "甲"),
                cue("b", 1800, 3000, "乙"),
            ])
        with self.assertRaises(ValueError):
            render_checked_candidates([
                cue("a", 0, 2000, "甲"),
                cue("a", 2000, 3000, "乙"),
            ])


if __name__ == "__main__":
    unittest.main()
