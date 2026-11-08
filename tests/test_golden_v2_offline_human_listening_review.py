"""Pure synthetic, offline human-listening review gate tests."""
from __future__ import annotations

import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.golden_v2_offline_human_listening_review import (
    create_template, validate_review, main,
)
from scripts.golden_v2_offline_srt_timeline_qa import (
    build_review_interval_manifest, parse_srt,
)


def srt():
    return (
        "1\n00:00:00,000 --> 00:00:01,000\n甲\n\n"
        "2\n00:00:21,000 --> 00:00:22,000\n乙\n\n"
        "3\n00:01:22,000 --> 00:01:23,000\n丙\n"
    )


class HumanListeningReviewTests(unittest.TestCase):
    def setUp(self):
        self.source_sha = "a" * 64
        self.hashes = ["b" * 64, "c" * 64]
        self.intervals = build_review_interval_manifest(
            parse_srt(srt()), audio_duration_ms=100000,
        )
        self.template = create_template(
            self.intervals, normalized_audio_sha256=self.source_sha,
            clip_sha256=self.hashes,
        )

    def test_template_requires_human_and_cannot_prepass_golden(self):
        result = self.template
        self.assertEqual(result["clip_count"], 2)
        self.assertFalse(result["human_listening_complete"])
        self.assertFalse(result["audio_alignment_verified"])
        self.assertFalse(result["golden_qa_pass"])
        self.assertFalse(result["publication_authorized"])
        self.assertEqual(result["records"][0]["speech_judgement"], "NOT_REVIEWED")
        self.assertEqual(result["records"][0]["exact_srt_gap_ms"], 20000)
        self.assertEqual(result["records"][0]["clip_sha256"], self.hashes[0])
        self.assertEqual(result["records"][1]["listen_end_ms"], 85000)
        pending = validate_review(result, result)
        self.assertEqual(pending["open_or_uncertain_count"], 2)
        self.assertEqual(pending["review_status"], "HUMAN_REVIEW_INCOMPLETE")

    def test_reviewer_annotations_still_not_release_permission(self):
        filled = copy.deepcopy(self.template)
        first, second = filled["records"]
        first.update(
            speech_judgement="SPEECH_CONFIRMED",
            source_audio_listened=True,
            reviewer_id="authorized-reviewer",
            reviewed_at_utc="2026-11-08T12:34:56Z",
            transcript_evidence_sha256="d" * 64,
            speech_anchor_ranges_ms=[
                {"start_ms": 5000, "end_ms": 9000},
                {"start_ms": 10000, "end_ms": 13000},
            ],
        )
        second.update(
            speech_judgement="NON_SPEECH_CONFIRMED",
            source_audio_listened=True,
            reviewer_id="authorized-reviewer",
            reviewed_at_utc="2026-11-08T12:45:00Z",
        )
        status = validate_review(filled, self.template)
        self.assertEqual(status["reviewed_count"], 2)
        self.assertTrue(status["all_intervals_reviewed"])
        self.assertEqual(status["open_or_uncertain_count"], 0)
        self.assertEqual(status["review_status"], "HUMAN_ANNOTATIONS_COMPLETE_NEEDS_QA")
        self.assertFalse(status["audio_alignment_verified"])
        self.assertFalse(status["golden_qa_pass"])
        self.assertFalse(status["publication_authorized"])

    def test_reject_invented_review_or_source_modification(self):
        forged = copy.deepcopy(self.template)
        forged["human_listening_complete"] = True
        with self.assertRaises(ValueError):
            validate_review(forged, self.template)
        forged = copy.deepcopy(self.template)
        forged["records"][0]["clip_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            validate_review(forged, self.template)
        forged = copy.deepcopy(self.template)
        forged["records"][0]["speech_judgement"] = "SPEECH_CONFIRMED"
        with self.assertRaises(ValueError):
            validate_review(forged, self.template)
        forged = copy.deepcopy(self.template)
        forged["records"][0]["reviewer_id"] = "name"
        with self.assertRaises(ValueError):
            validate_review(forged, self.template)

    def test_reject_ungrounded_word_ranges(self):
        doc = copy.deepcopy(self.template)
        row = doc["records"][0]
        row.update(
            speech_judgement="SPEECH_CONFIRMED",
            source_audio_listened=True,
            reviewer_id="a",
            reviewed_at_utc="2026-11-08T12:34:56Z",
            transcript_evidence_sha256="f" * 64,
            speech_anchor_ranges_ms=[{"start_ms": 0, "end_ms": 5000}],
        )
        with self.assertRaises(ValueError):
            validate_review(doc, self.template)
        row["speech_anchor_ranges_ms"] = [
            {"start_ms": 6000, "end_ms": 8000},
            {"start_ms": 7000, "end_ms": 8500},
        ]
        with self.assertRaises(ValueError):
            validate_review(doc, self.template)

    def test_strict_input_and_no_overwrite_cli(self):
        tampered = copy.deepcopy(self.intervals)
        tampered["total_srt_gap_ms"] += 100
        with self.assertRaises(ValueError):
            create_template(tampered, normalized_audio_sha256=self.source_sha,
                            clip_sha256=self.hashes)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "intervals.json"
            manifest.write_text(json.dumps(self.intervals), encoding="utf-8")
            hashes = root / "clip_shas.json"
            hashes.write_text(json.dumps(self.hashes), encoding="utf-8")
            output = root / "review.json"
            args = ["human-review", "--interval-manifest", str(manifest),
                    "--source-sha256", self.source_sha,
                    "--clip-shas-json", str(hashes),
                    "--output", str(output)]
            with patch("sys.argv", args), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
            self.assertTrue(output.exists())
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["clip_count"], 2)
            self.assertFalse(data["golden_qa_pass"])
            with patch("sys.argv", args), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), data)
            with patch("sys.argv", args[:-1] + [str(manifest)]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)


if __name__ == "__main__":
    unittest.main()
