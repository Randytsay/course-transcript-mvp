"""Golden24 V7: fail-closed real-timing one-line subtitle checks."""
import unittest

from app.providers.subtitle_single_line_gate import strict_srt_gate
from scripts.golden24.v7_single_line import paired_chirp_boundary,split_with_proof,safe_phrase_boundary


class SingleLineQA(unittest.TestCase):
    def test_production_output_validator_denies_multiline_and_overlong(self):
        import tempfile
        from pathlib import Path
        from app.providers.validate_outputs import _validate_srt
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"candidate.srt"
            p.write_text("1\n00:00:01,000 --> 00:00:02,500\n本法會圓滿。\n")
            self.assertTrue(_validate_srt(p,1))
            p.write_text("1\n00:00:01,000 --> 00:00:02,500\n本法會圓滿，\n大家感恩。\n")
            self.assertFalse(_validate_srt(p,1))
            p.write_text("1\n00:00:01,000 --> 00:00:02,500\n本法會圓滿，大家感恩今天所有來參加的貴賓與同修。\n")
            self.assertFalse(_validate_srt(p,1))

    def test_multiline_same_cue_is_unconditionally_rejected(self):
        cues=[{"start_ms":1000,"end_ms":4000,"text":"好好生活，\n善待自己。"}]
        r=strict_srt_gate(cues,strict_speech_coverage_verified=True)
        self.assertFalse(r["passed"])
        self.assertEqual(r["multiline_cues"],1)
        self.assertIn("multiline-cue-prohibited",
                      [e["reason"] for e in r["hard_gate_blockers"]])

    def test_overlong_one_line_is_not_silent_fallback(self):
        cues=[{"start_ms":1000,"end_ms":4000,"text":"這是一句很長很長的完整字幕，不該直接放行。"}]
        r=strict_srt_gate(cues,strict_speech_coverage_verified=True)
        self.assertFalse(r["passed"])
        self.assertGreater(r["overlong_cues"],0)

    def test_unproven_acoustic_gate_always_blocks(self):
        cues=[{"start_ms":0,"end_ms":1000,"text":"大家好。"}]
        r=strict_srt_gate(cues)
        self.assertFalse(r["passed"])
        self.assertIn("independent-full-audio-acoustic-coverage-not-passed",
                      [e["reason"] for e in r["hard_gate_blockers"]])

    def test_clean_independently_verified_one_line_can_pass_structure(self):
        cues=[{"start_ms":0,"end_ms":1400,"text":"大家好。"},
              {"start_ms":1400,"end_ms":3000,"text":"歡迎參加法會。"}]
        r=strict_srt_gate(cues,strict_speech_coverage_verified=True,
                          require_unresolved_zero=True,unresolved=[])
        self.assertTrue(r["passed"])

    def test_two_sided_word_evidence_only(self):
        words=[]
        for i,c in enumerate("我們要生活這個生活品質比較好一點"):
            words.append({"word":c,"start_ms":1000+i*200})
        left="我們要生活，"
        right="這個生活品質比較好一點。"
        ts,proof=paired_chirp_boundary(left,right,words,1000,8000)
        self.assertEqual(ts,1000+5*200)
        self.assertEqual(proof["reason"],"two-sided-unique-Chirp-3-word-anchor")
        self.assertGreaterEqual(proof["support_chars"],7)

    def test_no_char_ratio_timing_and_no_one_sided_guess(self):
        words=[{"word":c,"start_ms":1000+i*200}
               for i,c in enumerate("我們要生活但是聲音不清楚")]
        ts,proof=paired_chirp_boundary(
            "我們要生活，","這個生活品質比較好一點。",words,1000,7000)
        self.assertIsNone(ts)
        self.assertEqual(proof["reason"],"no-dual-sided-Chirp-anchor")

    def test_ambiguous_repeated_anchors_not_chosen(self):
        speech="我們要生活這個生活品質我們要生活這個生活品質"
        words=[{"word":c,"start_ms":1000+i*100} for i,c in enumerate(speech)]
        ts,reason=paired_chirp_boundary("我們要生活","這個生活品質",words,0,7000)
        self.assertIsNone(ts)
        self.assertEqual(reason["reason"],"ambiguous-audio-word-occurrence")

    def test_rejects_word_break_even_if_audio_time_exists(self):
        for a,b in [
            ("這部經是鳩摩","羅什尊者的著作，"),
            ("這個生活品質比較好","一點。"),
            ("世界上已經沒","有辦法超過"),
            ("但是現在無量淨居天","眾見到彌勒佛了"),
            ("所以說如來跟阿羅漢說未","來成佛，"),
        ]:
            yes,reason=safe_phrase_boundary(a,b)
            self.assertFalse(yes,(a,b,reason))
        good,reason=safe_phrase_boundary(
            "如來要入城了，趕快來迎接。",
            "還有下一段供養經文。")
        self.assertTrue(good,reason)

    def test_multi_split_not_partially_applied(self):
        words=[{"word":c,"start_ms":2000+i*120} for i,c in enumerate("我們要生活這個生活品質很好")]
        lines=["我們要生活","這個生活品質","未辨識短句"]
        steps,fail=split_with_proof(lines,2000,6500,0,{},words)
        self.assertEqual(steps,[])
        self.assertTrue(fail)


if __name__=="__main__":
    unittest.main()
