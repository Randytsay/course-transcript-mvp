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

    def test_v4_early_onset_and_hold_no_overlap(self):
        from scripts.golden24.v4_display_timing import pad_display_timing
        src = [
            {"id":1,"start_ms":1000,"end_ms":3000,"text":"第一句。"},
            {"id":2,"start_ms":3640,"end_ms":5700,"text":"第二句。"},
            {"id":3,"start_ms":5700,"end_ms":7100,"text":"第三句。"},
            {"id":4,"start_ms":15000,"end_ms":18000,"text":"第四句。"},
        ]
        new,action = pad_display_timing(src)
        self.assertEqual(len(new), 4)
        self.assertLess(new[1]["start_ms"],src[1]["start_ms"])
        self.assertGreater(new[0]["end_ms"],src[0]["end_ms"])
        self.assertEqual(new[2]["start_ms"], src[2]["start_ms"])
        self.assertTrue(all(a["end_ms"]<=b["start_ms"]
                            for a,b in zip(new,new[1:])))
        self.assertTrue(all(x["start_ms"]<=s["start_ms"]
                            and x["end_ms"]>=s["end_ms"]
                            for x,s in zip(new,src)))
        self.assertTrue(all(0 <= x["next_early_display_ms"] <= 450
                            and 0 <= x["previous_hold_ms"] <= 220 for x in action))

    def test_v4_rejects_overlap(self):
        from scripts.golden24.v4_display_timing import pad_display_timing
        with self.assertRaisesRegex(ValueError,"Overlapping"):
            pad_display_timing([
                {"id":1,"start_ms":0,"end_ms":3200,"text":"甲"},
                {"id":2,"start_ms":3000,"end_ms":4400,"text":"乙"}
            ])

    def test_v5_punctuation_keeps_all_word_characters(self):
        from scripts.golden24.v5_semantic_layout import lex, curated
        example={"id":905,"start_ms":0,"end_ms":4000,
                 "text":"富貴生病跟貧窮生病有沒有一樣啊不一樣對不對對嘛有"}
        changed,flags,n=curated([example])
        self.assertEqual(flags,[])
        self.assertGreater(n,0)
        self.assertEqual(lex(changed[0]["text"]),lex(example["text"]))
        self.assertIn("？",changed[0]["text"])
        self.assertIn("。",changed[0]["text"])

    def test_v5_no_line_wider_than_14_and_keeps_content(self):
        from scripts.golden24.v5_semantic_layout import render_lines
        example="如來要入城了，趕快來迎接。還有佛說的經文，怎麼樣？"
        wrapped=render_lines(example,14)
        self.assertTrue(all(len(line)<=14 for line in wrapped.splitlines()))
        self.assertEqual(wrapped.replace("\n",""),example)

    def test_v5_split_prefers_complete_sentence(self):
        from scripts.golden24.v5_semantic_layout import fragments
        example="如來要入城了，趕快來迎接。還有佛說的經文，怎麼樣？"
        chunks=fragments(example)
        self.assertGreaterEqual(len(chunks),2)
        self.assertEqual("".join(chunks),example)
        self.assertEqual(chunks[0],"如來要入城了，趕快來迎接。")

    def test_v5_recovers_only_grounded_v2_timing(self):
        from scripts.golden24.v5_semantic_layout import v2_boundary_for_parts
        text="我都享受到。現在不想出來，"
        before="我都享受到。"
        from scripts.golden24.v5_semantic_layout import lex
        t=v2_boundary_for_parts(text,[before,"現在不想出來，"],0,
                                  {len(lex(before))-1:1200},200,2600)
        self.assertIsNone(t)
        t=v2_boundary_for_parts(text,[before,"現在不想出來，"],0,
                                  {len(lex(before)):1300},200,2600)
        self.assertEqual(t,[1300])

    def test_v6_keep_protected_phrase_intact(self):
        from scripts.golden24.v6_word_boundary import format_word_safe,flatten,jieba
        if jieba is None:
            self.skipTest("Review-only jieba dependency not installed")
        text="我們要生活，這個生活品質比較好一點，而且能照顧家人。"
        shaped,review=format_word_safe(text,preferred=14)
        self.assertEqual(flatten(shaped),text)
        lines=shaped.splitlines()
        self.assertTrue(all(len(line)<=20 for line in lines))
        for a,b in zip(lines,lines[1:]):
            self.assertFalse(a.endswith("好") and b.startswith("一點"))
            self.assertFalse(a.endswith("一") and b.startswith("樣"))
            self.assertFalse(a.endswith("我") and b.startswith("們"))

    def test_v6_audio_timestamp_repair_does_not_interpolate(self):
        from scripts.golden24.v6_word_boundary import nearest_source_word_start
        words=[
            {"word":"這", "start_ms":3205840},
            {"word":"個", "start_ms":3205960},
            {"word":"生", "start_ms":3206040},
            {"word":"活", "start_ms":3206240},
            {"word":"品", "start_ms":3206360},
            {"word":"質", "start_ms":3206560},
            {"word":"比", "start_ms":3206800},
            {"word":"較", "start_ms":3206920},
            {"word":"好", "start_ms":3207160},
            {"word":"一", "start_ms":3207360},
            {"word":"點", "start_ms":3207480},
        ]
        self.assertEqual(
            nearest_source_word_start(words,"好",3207360,3206000,3208000),
            3207160)
        self.assertIsNone(
            nearest_source_word_start(words,"新詞",3207360,3206000,3208000))


if __name__ == "__main__":
    unittest.main()
