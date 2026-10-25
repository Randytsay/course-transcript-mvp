"""No-network, no-provider acceptance tests for independent limited-AI delivery."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, STAGES, V2Conflict
from app.pipeline.golden_v2_ai_limited_closeout import (
    TIER, prepare_limited_manifest, register_proposal, finalize_readback,
)


class AILimitedCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job_id = "v2p_"+"a"*32
        self.folder = self.root/"jobs"/self.job_id
        self.folder.mkdir(parents=True)
        self.audio = b"normalized synthetic test audio, not real speech"
        self.sha = hashlib.sha256(self.audio).hexdigest()
        (self.folder/"normalized.flac").write_bytes(self.audio)
        (self.folder/"source-preflight.json").write_text(json.dumps({
            "source_sha256": self.sha,
            "drive_observation":{"file_id":"f123","version":"7"},
        }))
        self.srt = self.folder/"original_review.srt"
        self.srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n佛法\n\n"
                            "2\n00:00:01,100 --> 00:00:02,000\n修行\n")
        self.txt = self.folder/"review_transcript.txt"
        self.txt.write_text("佛法\n修行\n")
        self.qa = self.folder/"source_acoustic_triage.json"
        self.qa.write_text(json.dumps({
            "job_id":self.job_id,"srt_sha256":hashlib.sha256(self.srt.read_bytes()).hexdigest(),
            "total_acoustic_gaps":2,
            "all_acoustic_gaps":[
                {"start_ms":2000,"end_ms":5000,"acoustic_disposition":"REVIEW",
                 "classification":"NO_SRT_TEXT_SHORT_GAP"},
                {"start_ms":5000,"end_ms":7000,
                 "acoustic_disposition":"NONSPEECH_CONFIRMED",
                 "classification":"NO_SRT_TEXT_SHORT_GAP"}],
        }))
        self.ledger = GoldenV2Ledger(self.root/"ledger.sqlite")
        with sqlite3.connect(self.root/"ledger.sqlite") as db:
            db.execute(
                "INSERT INTO v2_jobs(id,source_sha,mode,revision,approval_reference,"
                "approved_cents,estimated_cents,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (self.job_id,self.sha,"FLASH_CHIRP",9,"approved",100,100,
                 "NEEDS_REVIEW","2026-10-25T00:00:00+00:00")
            )
            for ix,name in enumerate(STAGES):
                state = "REVIEW_REQUIRED" if name in (
                    "chirp_independent","gemini_primary") else "PENDING"
                db.execute("INSERT INTO v2_stages(job_id,ord,name,state,estimate_cents,"
                           "actual_cents,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (self.job_id,ix,name,state,0,0,"2026-10-25T00:00:00+00:00"))
            db.execute(
                "INSERT INTO v2_chirp_operations "
                "(job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,"
                "status,operation_name,request_id,output_uri,estimated_cents,"
                "purpose,result_sha,actual_cents,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.job_id,"win0",self.sha,"b"*64,"gs://test/audio",0,3000,
                 "DONE","operation-123","req01","gs://test/out",1,
                 "BASE","c"*64,1,"2026-10-25T00:00:00+00:00")
            )
        self.names = ("source_AI校對_含待複核.srt","source_AI校對_含待複核.txt")

    def proposal(self):
        return prepare_limited_manifest(
            root=self.root,job_id=self.job_id,
            srt_path=self.srt,txt_path=self.txt,
            triage_path=self.qa,remote_names=self.names,
        )

    def test_independent_ledger_stage_remains_review(self):
        manifest=self.proposal()
        self.assertEqual(manifest["quality_tier"],TIER)
        self.assertEqual(len(manifest["open_items"]),1)
        self.assertFalse(manifest["publication_granted"])
        first=register_proposal(self.root,manifest)
        second=register_proposal(self.root,manifest)
        self.assertEqual(first,second)
        self.assertEqual(first["state"],"PREPARED")
        self.assertEqual(self.ledger.status(self.job_id)["status"],"NEEDS_REVIEW")
        with sqlite3.connect(self.root/"ledger.sqlite") as db:
            self.assertEqual(db.execute(
                "select count(*) from v2_ai_limited_deliveries").fetchone()[0],1)
            self.assertEqual(db.execute(
                "select state from v2_stages where job_id=? and name='golden_qa'",
                (self.job_id,)).fetchone()[0],"PENDING")

    def test_source_drift_and_unfinished_provider_halt(self):
        with sqlite3.connect(self.root/"ledger.sqlite") as db:
            db.execute("UPDATE v2_chirp_operations SET status='SUBMITTED'")
        with self.assertRaisesRegex(V2Conflict,"unfinished"):
            self.proposal()
        with sqlite3.connect(self.root/"ledger.sqlite") as db:
            db.execute("UPDATE v2_chirp_operations SET status='DONE'")
        self.audio=b"changed"
        (self.folder/"normalized.flac").write_bytes(self.audio)
        with self.assertRaisesRegex(V2Conflict,"not job source"):
            self.proposal()

    def test_text_mismatch_and_triage_drift_fail_closed(self):
        self.txt.write_text("佛法\n錯字\n")
        with self.assertRaisesRegex(V2Conflict,"SRT and TXT"):
            self.proposal()
        self.txt.write_text("佛法\n修行\n")
        q=json.loads(self.qa.read_text());q["srt_sha256"]="f"*64
        self.qa.write_text(json.dumps(q))
        with self.assertRaisesRegex(V2Conflict,"QA/triage"):
            self.proposal()

    def test_prepared_does_not_imply_drive_delivery(self):
        manifest=self.proposal()
        register_proposal(self.root,manifest)
        remote=[{"name":self.names[0],"id":"driveSRT"},
                {"name":self.names[1],"id":"driveTXT"}]
        grant={"job_id":self.job_id,"manifest_sha256":manifest["manifest_sha256"],
               "quality_tier":TIER,"source_sha256":manifest["source_sha256"],
               "output_names":manifest["output_names"],"approve_delivery":True,
               "actor":"authenticated-test-owner","reference":"test-owner-grant"}
        with self.assertRaisesRegex(V2Conflict,"source-bound owner"):
            finalize_readback(root=self.root,manifest=manifest,remote_files=remote,
                              owner_grant=grant,signed_grant_verifier=lambda _:False,
                              readback_verifier=lambda *_:True)
        with self.assertRaisesRegex(V2Conflict,"readback"):
            finalize_readback(root=self.root,manifest=manifest,remote_files=remote,
                              owner_grant=grant,signed_grant_verifier=lambda _:True,
                              readback_verifier=lambda *_:False)
        first=finalize_readback(root=self.root,manifest=manifest,remote_files=remote,
                               owner_grant=grant,signed_grant_verifier=lambda _:True,
                               readback_verifier=lambda name,_,sha:(
                                   sha==manifest["srt_sha256"] if name.endswith(".srt")
                                   else sha==manifest["txt_sha256"]))
        self.assertEqual(first["golden_status_unchanged"],"NEEDS_REVIEW")
        self.assertEqual(first,finalize_readback(
            root=self.root,manifest=manifest,remote_files=remote,
            owner_grant=grant,signed_grant_verifier=lambda _:True,
            readback_verifier=lambda *_:True))
        self.assertEqual(self.ledger.status(self.job_id)["status"],"NEEDS_REVIEW")

    def test_no_fake_golden_or_human_flags(self):
        manifest=self.proposal()
        altered=dict(manifest)
        altered["golden_qa_claim"]="PASS"
        with self.assertRaisesRegex(V2Conflict,"forged or altered"):
            register_proposal(self.root,altered)
        self.assertEqual(manifest["human_listening_claim"],"NOT_CLAIMED")
        self.assertEqual(manifest["golden_qa_claim"],"NOT_CLAIMED")


if __name__ == "__main__":
    unittest.main()
