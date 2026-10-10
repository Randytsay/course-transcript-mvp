"""Golden punctuation repair previews must not alter words or timestamps."""
import importlib.util
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/"scripts"/"golden_punctuation_revision.py"
SPEC=importlib.util.spec_from_file_location("golden_punctuation_revision", SCRIPT)
MOD=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class PunctuationRevisionTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.d=Path(self.t.name)
        document={
          "segments":[{"segment_id":"seg-0001","start_ms":1000,"end_ms":2000,"text":"原始語句？"}],
          "display_segments":[{"segment_id":"seg-0001","start_ms":1000,"end_ms":2000,"cleaned_text":"好嗎？，"},
                              {"segment_id":"seg-0002","start_ms":2000,"end_ms":3000,"cleaned_text":"應該；，"}]
        }
        self.document=document
        (self.d/"subtitles-cleaned.json").write_text(json.dumps(document,ensure_ascii=False))
        srt,txt=MOD.outputs(document)
        (self.d/"subtitles-cleaned.srt").write_bytes(srt)
        (self.d/"transcript-cleaned.txt").write_bytes(txt)

    def tearDown(self):
        self.t.cleanup()

    def test_preview_fixes_only_duplicate_punctuation(self):
        result=MOD.preview(self.d)
        self.assertEqual(len(result["changes"]),2)
        self.assertTrue(result["raw_segments_preserved"])
        self.assertTrue(result["timecodes_preserved"])
        self.assertEqual(result["changes"][0]["after"],"好嗎？")
        self.assertEqual(result["changes"][1]["after"],"應該；")
        self.assertNotEqual(result["new_data"]["subtitles-cleaned.srt"],
                            (self.d/"subtitles-cleaned.srt").read_bytes())
        self.assertEqual(self.document["segments"],json.loads(
            result["new_data"]["subtitles-cleaned.json"])["segments"])

    def test_dryrun_does_not_mutate(self):
        before={p.name:p.read_bytes() for p in self.d.iterdir()}
        MOD.preview(self.d)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.d.iterdir()})

    def test_fail_closed_when_srt_does_not_match_json(self):
        (self.d/"subtitles-cleaned.srt").write_text("not the rendered SRT")
        with self.assertRaisesRegex(ValueError,"SRT differs"):
            MOD.preview(self.d)

    def test_fail_closed_when_text_does_not_match_json(self):
        (self.d/"transcript-cleaned.txt").write_text("corrupt")
        with self.assertRaisesRegex(ValueError,"TXT differs"):
            MOD.preview(self.d)

    def test_idempotent_second_preview(self):
        modified=MOD.preview(self.d)
        (self.d/"subtitles-cleaned.json").write_bytes(modified["new_data"]["subtitles-cleaned.json"])
        (self.d/"subtitles-cleaned.srt").write_bytes(modified["new_data"]["subtitles-cleaned.srt"])
        (self.d/"transcript-cleaned.txt").write_bytes(modified["new_data"]["transcript-cleaned.txt"])
        result=MOD.preview(self.d)
        self.assertFalse(result["changes"])

    def prepare_apply(self):
        root = self.d / "data"
        target = root / "jobs" / "250301-test-job"
        target.mkdir(parents=True)
        for key in ("subtitles-cleaned.json", "subtitles-cleaned.srt", "transcript-cleaned.txt"):
            (target/key).write_bytes((self.d/key).read_bytes())
        (target/"golden-review-provenance.json").write_text(json.dumps({"semantic_review_completed":False}))
        qa_before={"status":"REVIEW", "review_required":["subtitle contains repeated punctuation: seg-0001", "subtitle contains repeated punctuation: seg-0002", "sound", "density"]}
        (target/"qa-report.json").write_text(json.dumps(qa_before))
        (root/"golden-runner").mkdir()
        (root/"golden-runner"/"runner.lock").touch()
        db=sqlite3.connect(root/"course-transcript.db")
        db.execute("CREATE TABLE jobs (id TEXT, batch_id TEXT, status TEXT, locked_by TEXT)")
        db.execute("INSERT INTO jobs VALUES (?,?,?,?)",(target.name,MOD.BATCH,"awaiting_review",None))
        db.commit()
        db.close()
        return root,target,qa_before

    def test_apply_changes_with_backup_and_qa_improvement(self):
        root,target,before_qa=self.prepare_apply()
        before=MOD.preview(target)
        with patch.object(MOD.subprocess,"run") as run:
            def fake_qa(cmd,**kwargs):
                (target/"qa-report.json").write_text(json.dumps({"status":"REVIEW", "review_required":["sound", "density"]}))
                return subprocess.CompletedProcess(cmd,0,"","")
            run.side_effect=fake_qa
            r=MOD.apply(root,target.name,before["source_sha256"],root/"app/providers/qa_report.py")
        self.assertTrue(r["changed"])
        self.assertEqual(r["old_reviews"],4)
        self.assertEqual(r["new_reviews"],2)
        self.assertEqual(len(MOD.preview(target)["changes"]),0)
        backup=Path(r["backup"])
        self.assertEqual(json.loads((backup/"qa-report.json").read_text()),before_qa)
        self.assertEqual(json.loads((backup/"manifest.json").read_text())["status"],"QA_REVIEW_IMPROVED")

    def test_apply_rolls_back_when_qa_is_not_improved(self):
        root,target,_=self.prepare_apply()
        baseline={name:(target/name).read_bytes() for name in ("subtitles-cleaned.json", "subtitles-cleaned.srt", "transcript-cleaned.txt", "qa-report.json")}
        with patch.object(MOD.subprocess,"run") as run:
            def no_improvement(cmd,**kwargs):
                (target/"qa-report.json").write_text(json.dumps({"status":"REVIEW", "review_required":["subtitle contains repeated punctuation: seg-0001", "subtitle contains repeated punctuation: seg-0002", "sound", "density"]}))
                return subprocess.CompletedProcess(cmd,0,"","")
            run.side_effect=no_improvement
            with self.assertRaisesRegex(RuntimeError,"Unexpected QA issue change"): 
                MOD.apply(root,target.name,MOD.preview(target)["source_sha256"],root/"app/providers/qa_report.py")
        self.assertEqual(baseline,{name:(target/name).read_bytes() for name in baseline})
        journals=list((target/"backups").glob("pre-punctuation-*/manifest.json"))
        self.assertEqual(len(journals),1)
        self.assertEqual(json.loads(journals[0].read_text())["status"],"ROLLED_BACK")

    def test_apply_rejects_stale_source(self):
        root,target,_=self.prepare_apply()
        with self.assertRaisesRegex(ValueError,"Source hash changed"):
            MOD.apply(root,target.name,"0"*64,root/"app/providers/qa_report.py")
        self.assertFalse((target/"backups").exists())


if __name__=="__main__":
    unittest.main()