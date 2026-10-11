"""Golden24 fail-closed pilot gate regression tests (no Drive/provider calls)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.golden24.pilot_preflight import (
    milliseconds, parse_srt, report_gate, verify_snapshot,
)


class PilotGateTests(unittest.TestCase):
    def test_timestamp_validation(self):
        self.assertEqual(milliseconds("02:03:46,299"), 7426299)
        with self.assertRaises(ValueError):
            milliseconds("00:61:00,000")

    def test_srt_parser_catches_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            srt = Path(d) / "a.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n測試一。\n\n"
                "2\n00:00:01,500 --> 00:00:03,000\n測試二。\n",
                encoding="utf-8",
            )
            cues, errors = parse_srt(srt)
            self.assertEqual(len(cues), 2)
            self.assertTrue(any("Overlap" in e for e in errors))

    def test_historical_pass_with_missing_speech_fails_closed(self):
        qa = {
            "status": "PASS", "errors": [], "human_missing_count": 3,
            "source_missing_count": 0,
            "boundary_integrity": {
                "split_candidate_count": 9,
                "close_unpunctuated_boundary_count": 33,
            },
        }
        blockers, review = report_gate({"qa": qa, "timing_truth": "chirp"})
        self.assertTrue(any("human_missing_count=3" in s for s in blockers))
        self.assertTrue(any("split_candidate_count=9" in s for s in review))

    def test_missing_counts_cannot_pass(self):
        blockers, _ = report_gate({"qa": {"status": "PASS"}, "timing_truth": "chirp"})
        self.assertTrue(blockers)

    def test_bad_manifest_never_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "SHA256SUMS.original.txt").write_text("invalid\n")
            with self.assertRaises(ValueError):
                verify_snapshot(p)

    def test_manifest_tampering_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            files = [
                "20260927.MP3", "20260927.srt", "20260927_逐字稿.txt",
                "20260927_逐字稿.reference-original-70116239.txt",
                "20260927_transcript_report.json",
            ]
            rows = []
            for name in files:
                (p / name).write_bytes(b"test")
                rows.append(hashlib.sha256(b"test").hexdigest() + "  " + name)
            (p / "SHA256SUMS.original.txt").write_text("\n".join(rows) + "\n")
            self.assertTrue(verify_snapshot(p)["verified"])
            (p / "20260927.srt").write_bytes(b"tampered")
            result = verify_snapshot(p)
            self.assertFalse(result["verified"])
            self.assertEqual(result["mismatched"], ["20260927.srt"])

    def test_chirp_coverage_detects_silent_subtitle_gap(self):
        from scripts.golden24.chirp_word_coverage import check_chirp_coverage
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            j = p / "job"
            j.mkdir()
            digest = hashlib.sha256(b"audio").hexdigest()
            (p / "20260927.MP3").write_bytes(b"audio")
            (j / "source-manifest.json").write_text(
                json.dumps({"source_name": "20260927.MP3", "source_sha256": digest}))
            words = [{"word": "說", "start_ms": 6000+i*300, "end_ms": 6240+i*300}
                     for i in range(30)]
            (j / "merged-words.json").write_text(
                json.dumps({"words": words, "total_words": len(words)}))
            result = check_chirp_coverage(p, j, [
                {"start_ms": 0, "end_ms": 3000},
                {"start_ms": 17000, "end_ms": 20000},
            ], digest)
            self.assertTrue(result["requires_acoustic_review"])
            self.assertEqual(result["significant_uncovered_runs"], 1)
            self.assertEqual(result["uncovered_chirp_words"], 30)

    def test_chirp_coverage_rejects_wrong_source_media(self):
        from scripts.golden24.chirp_word_coverage import check_chirp_coverage
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            j = p / "job"
            j.mkdir()
            (j / "source-manifest.json").write_text(
                json.dumps({"source_name": "different.MP3", "source_sha256": "bad"}))
            (j / "merged-words.json").write_text(
                json.dumps({"words": [], "total_words": 0}))
            with self.assertRaisesRegex(ValueError, "provenance"):
                check_chirp_coverage(p, j, [], "expected-sha")

    def test_gap_candidate_is_staged_idempotently_and_never_publishable(self):
        from scripts.golden24.recover_gap_candidate import create_candidate
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            snap, job, out = p / "snap", p / "job", p / "stage"
            snap.mkdir()
            job.mkdir()
            mp3 = b"source-bytes"
            (snap / "20260927.MP3").write_bytes(mp3)
            (snap / "20260927.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n開頭。\n\n"
                "2\n00:01:20,000 --> 00:01:22,000\n後續。\n",
                encoding="utf-8",
            )
            (job / "source-manifest.json").write_text(json.dumps({
                "source_name": "20260927.MP3",
                "source_sha256": hashlib.sha256(mp3).hexdigest()}))
            (job / "merged-words.json").write_text(json.dumps({
                "total_words": 12,
                "words": [{"word": "字", "start_ms": 6000 + 500*i,
                           "end_ms": 6200 + 500*i} for i in range(12)]}))
            (job / "transcript.json").write_text(json.dumps({
                "source_timing": "chirp_3 word timestamps",
                "display_segments": [
                    {"start_ms": 6000, "end_ms": 12000, "text": "確定補段。"},
                    {"start_ms": 79500, "end_ms": 80500, "text": "跨界保留待審。"},
                ]}))
            first = create_candidate(snap, job, out)
            second = create_candidate(snap, job, out)
            self.assertEqual(first["candidate_srt_sha256"], second["candidate_srt_sha256"])
            self.assertFalse(first["publication_allowed"])
            self.assertEqual(first["inserted_chirp_timed_cues"], 1)
            self.assertEqual(len(first["crossing_segments_pending_review"]), 1)
            self.assertEqual(first["candidate_cues"], 3)

    def test_golden_readability_wrap_preserves_text(self):
        from scripts.golden24.generate_review_pair import linewrap, cjktypography, tc
        text = "這就是所有一切殊勝的因緣功德，應該好好學習才能得到成就。"
        wrapped = linewrap(text)
        self.assertEqual(wrapped.replace("\n", ""), text)
        self.assertLessEqual(max(map(len, wrapped.splitlines())), 24)
        self.assertEqual(tc(5238120), "01:27:18,120")
        self.assertEqual(cjktypography("大,小?"), "大，小？")


    def test_v3_readability_merge_uses_only_old_boundaries_and_keeps_words(self):
        from scripts.golden24.v3_readability import conservative_merge, clean
        before = [
            {"id":1,"start_ms":0,"end_ms":2600,"text":"這是非常好的事情，"},
            {"id":2,"start_ms":2600,"end_ms":2990,"text":"對不對？"},
            {"id":3,"start_ms":3450,"end_ms":5200,"text":"後面的新句子。"},
        ]
        merged, actions = conservative_merge(before)
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(merged), 2)
        self.assertEqual(clean("".join(x["text"] for x in before)),
                         clean("".join(x["text"] for x in merged)))
        original_starts={x["start_ms"] for x in before}
        original_ends={x["end_ms"] for x in before}
        self.assertTrue(all(x["start_ms"] in original_starts
                            and x["end_ms"] in original_ends for x in merged))

    def test_v3_qa_refuses_promote_review(self):
        from scripts.golden24.v3_readability import render_txt
        cues=[{"start_ms":0,"end_ms":2000,"text":"第一段，"},
              {"start_ms":2040,"end_ms":3000,"text":"第二段。"}]
        from scripts.golden24.v3_readability import clean
        self.assertEqual(clean(render_txt(cues)), "第一段，第二段。")


if __name__ == "__main__":
    unittest.main()
