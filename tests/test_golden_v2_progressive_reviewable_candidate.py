"""Reviewable candidate delivery is independent from Golden acceptance.

All tests use synthetic text and timecodes, no private lecture data.
"""
from __future__ import annotations
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.golden_v2_progressive_reviewable_candidate import (
    create_reviewable_candidate, main,
)
from scripts.golden_v2_offline_srt_timeline_qa import parse_srt, _visible


def block(n, start, end, text):
    return f"{n}\n{start} --> {end}\n{text}"


class ProgressiveCandidateTests(unittest.TestCase):
    def test_draft_delivers_even_with_unanchored_gaps(self):
        text = "修" * 500
        src = (block(1, "00:00:01,000", "00:00:04,000", text)
               + "\n\n"
               + block(2, "00:00:40,000", "00:00:42,000", "尾聲。"))
        srt, meta = create_reviewable_candidate(
            src, corrected_txt=text + "尾聲。")
        cues = parse_srt(srt)
        self.assertEqual(meta["delivery_status"], "DRAFT_READY_WITH_REVIEW_FLAGS")
        self.assertEqual(len(meta["owner_review_flags"]), 1)
        self.assertGreater(len(meta["provisional_retimed_spans"]), 0)
        self.assertEqual(meta["placeholder_cue_count"], 0)
        self.assertEqual(_visible("".join(c.text for c in cues)), text + "尾聲。")
        self.assertTrue(meta["candidate_output_available"])
        self.assertFalse(meta["human_listening_required_before_draft_output"])
        self.assertFalse(meta["audio_alignment_verified"])
        self.assertFalse(meta["golden_qa_pass"])
        self.assertFalse(meta["drive_publication_authorized"])

    def test_short_flagged_region_uses_placeholder_not_fake_speech(self):
        src = (block(1, "00:00:00,000", "00:00:01,000", "甲")
               + "\n\n"
               + block(2, "00:00:30,000", "00:00:31,000", "乙"))
        candidate, meta = create_reviewable_candidate(src, corrected_txt="甲乙")
        self.assertEqual(meta["placeholder_cue_count"], 1)
        self.assertIn("待核對 01", candidate)
        self.assertEqual(meta["owner_review_flags"][0]["exact_gap_ms"], 29000)
        self.assertEqual(len(parse_srt(candidate)), 3)
        self.assertFalse(meta["golden_qa_pass"])

    def test_dense_medium_cue_splits_but_remaining_gap_is_flagged(self):
        txt = "佛" * 60
        source = (block(1, "00:00:01,000", "00:00:02,500", txt)
                  + "\n\n"
                  + block(2, "00:02:00,000", "00:02:02,000", "後。"))
        output, meta = create_reviewable_candidate(
            source, corrected_txt=txt + "後。")
        self.assertEqual(len(meta["owner_review_flags"]), 1)
        self.assertEqual(meta["placeholder_cue_count"], 1)
        self.assertGreater(len(meta["provisional_retimed_spans"]), 0)
        self.assertFalse(meta["golden_qa_pass"])

    def test_clean_source_is_also_deliverable_but_not_golden(self):
        source = (block(1, "00:00:00,000", "00:00:03,000", "甲")
                  + "\n\n"
                  + block(2, "00:00:03,050", "00:00:06,000", "乙"))
        output, meta = create_reviewable_candidate(source)
        self.assertEqual(meta["delivery_status"], "DRAFT_READY_FOR_OWNER_REVIEW")
        self.assertEqual(meta["owner_review_flags"], [])
        self.assertFalse(meta["golden_qa_pass"])
        self.assertEqual(len(parse_srt(output)), 2)

    def test_guarded_cli_create_new_only_and_return_zero_with_review_flags(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "original.srt"
            source.write_text(
                block(1, "00:00:01,000", "00:00:02,000", "甲") + "\n\n" +
                block(2, "00:00:21,000", "00:00:22,000", "乙"), encoding="utf-8")
            out = root / "review.srt"
            meta = root / "review.json"
            args = ["candidate","--srt",str(source),
                    "--candidate-output",str(out),"--manifest-output",str(meta)]
            with patch("sys.argv",args), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)  # flagged draft is NOT blocked
            self.assertTrue(out.exists())
            self.assertEqual(json.loads(meta.read_text())["delivery_status"],
                             "DRAFT_READY_WITH_REVIEW_FLAGS")
            with patch("sys.argv",args), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)  # no overwrite
            self.assertIn("甲",source.read_text(encoding="utf-8"))

    def test_source_text_mismatch_and_overlap_fail_closed(self):
        src = block(1,"00:00:00,000","00:00:01,000","甲")
        with self.assertRaises(ValueError):
            create_reviewable_candidate(src, corrected_txt="乙")
        broken = (src+"\n\n"+block(2,"00:00:00,900",
                                     "00:00:02,000","乙"))
        with self.assertRaises(ValueError):
            create_reviewable_candidate(broken)


if __name__ == "__main__":
    unittest.main()
