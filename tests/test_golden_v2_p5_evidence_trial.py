import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

F = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "golden_v2_p5_evidence_trial.py"
spec = importlib.util.spec_from_file_location("p5_evidence_trial", F)
p5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p5)

AUDIO=b"f"*180000
S=p5.sha256(AUDIO)


def example():
    p={"source_sha256":S,"model":p5.MODEL_PRIMARY,"status":"COMPLETE","model_text":"所以比如有長行，佛學講座測試文字。"*4}
    c={"source_sha256":S,"model":p5.MODEL_TIMING,"status":"COMPLETE",
       "transcript":"所以這裡有長行的，佛學講座測試文字。"*4,
       "words":[{"word":"所以","start_seconds":0,"end_seconds":1},
                {"word":"比如有長行","start_seconds":1,"end_seconds":2}],
       "word_time_offsets_verified":True,"billed_duration_seconds":240}
    rawp=json.dumps(p,ensure_ascii=False).encode()
    rawc=json.dumps(c,ensure_ascii=False).encode()
    t={"source_sha256":S,"model":p5.MODEL_THIRD,"status":"COMPLETE",
       "transcript":"所以比喻有長行的，佛學講座測試文字。"*4,
       "chirp_remains_time_authority":True,
       "previous_receipts":[{"model":p5.MODEL_PRIMARY,"receipt_sha256":p5.sha256(rawp)},
                            {"model":p5.MODEL_TIMING,"receipt_sha256":p5.sha256(rawc)}]}
    r={"clip_sha256":S,"source_start_seconds":600,"reviewed_sections":12,
       "total_sections":12,"unreviewed_sections":[],"srt_status":"DRAFT_NEEDS_SEGMENT_ALIGNMENT_AND_ACCEPTANCE",
       "choices":{str(i):"gemini" if i==3 else "both_ok" for i in range(1,13)},
       "human_confirmed":[{"section":9,"status":"ACCEPTED_USER_WORDING",
                           "correct_phrase":"所以比如有長行","previous_chirp_phrase":"所以這裡有長行的"}]}
    srt="1\n00:00:00,000 --> 00:00:03,000\n所以比如有長行，佛學講座測試文字。\n\n2\n00:00:05,000 --> 00:00:06,000\n繼續講述。\n"
    raw_srt=srt.replace("所以比如有長行","所以這裡有長行的")
    return {"audio":AUDIO,"primary_raw":rawp,"chirp_raw":rawc,
            "third_raw":json.dumps(t,ensure_ascii=False).encode(),
            "review_raw":json.dumps(r,ensure_ascii=False).encode(),"reviewed_srt":srt,"chirp_srt":raw_srt}


class EvidenceP5Tests(unittest.TestCase):
    def test_complete_preference_does_not_grant_gold(self):
        out=p5.validate_payload(**example())
        q=out["golden-qa-report.json"]
        self.assertFalse(q["golden_pass"])
        self.assertFalse(q["safe_publish_allowed"])
        self.assertEqual(q["owner_segment_preferences_recorded"],12)
        self.assertEqual(q["explicit_owner_corrections"],1)
        self.assertIn("NO_VAD_VS_CHIRP_GAP_COVERAGE",q["blocking_reasons"])
        self.assertEqual(len(q["possible_unreviewed_acoustic_gaps"]),1)
        self.assertIn("所以比如有長行",out["golden-candidate-REVIEW_ONLY.srt"])
        self.assertEqual(p5.normalized(out["golden-candidate-REVIEW_ONLY.txt"]),
                         p5.normalized("所以比如有長行，佛學講座測試文字。繼續講述。"))
    def test_source_mismatch_fails_closed(self):
        v=example();v["audio"]=b"changed"*30000
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"source"):
            p5.validate_payload(**v)
    def test_bad_or_missing_human_choice_rejected(self):
        v=example();r=json.loads(v["review_raw"]);r["choices"].pop("3")
        v["review_raw"]=json.dumps(r).encode()
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"12"):
            p5.validate_payload(**v)
    def test_uncorrected_human_phrase_rejected(self):
        v=example();v["reviewed_srt"]=v["reviewed_srt"].replace("所以比如有長行","所以這裡有長行的")
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"correction"):
            p5.validate_payload(**v)
    def test_chirp_time_source_enforced(self):
        v=example();c=json.loads(v["chirp_raw"]);c["words"][0]["end_seconds"]=260
        v["chirp_raw"]=json.dumps(c).encode()
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"Chirp"):
            p5.validate_payload(**v)
    def test_srt_overlap_rejected(self):
        v=example();v["reviewed_srt"]=v["reviewed_srt"].replace("00:00:05,000","00:00:02,500")
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"overlapping"):
            p5.validate_payload(**v)
    def test_prior_receipt_hash_mismatch_fails_closed(self):
        v=example();t=json.loads(v["third_raw"]);t["previous_receipts"][0]["receipt_sha256"]="a"*64
        v["third_raw"]=json.dumps(t).encode()
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"previous evidence"):
            p5.validate_payload(**v)
    def test_timing_change_is_rejected(self):
        v=example();v["reviewed_srt"]=v["reviewed_srt"].replace("00:00:05,000", "00:00:05,010")
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"acoustic cue"):
            p5.validate_payload(**v)
    def test_nonapproved_lexical_change_rejected(self):
        v=example();v["reviewed_srt"]=v["reviewed_srt"].replace("繼續講述", "完全不同")
        with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"unapproved lexical"):
            p5.validate_payload(**v)
    def test_create_only_output(self):
        artifacts=p5.validate_payload(**example())
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/"candidate"
            p5.write_create_only(out,artifacts)
            self.assertEqual(len(list(out.iterdir())),5)
            with self.assertRaisesRegex(p5.GoldenP5EvidenceError,"exists"):
                p5.write_create_only(out,artifacts)


if __name__=="__main__":
    unittest.main()

