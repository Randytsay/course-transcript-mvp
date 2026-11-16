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
        self.assets = self.folder / "golden-v2"
        self.assets.mkdir()
        self.audio = b"normalized synthetic test audio, not real speech"
        self.sha = hashlib.sha256(self.audio).hexdigest()
        (self.folder/"normalized.flac").write_bytes(self.audio)
        (self.folder/"source-preflight.json").write_text(json.dumps({
            "source_sha256": self.sha,
            "drive_observation":{"file_id":"f123","version":"7",
                                 "size_bytes":len(self.audio),"downloaded_md5":"unit-md5"},
            "drive_source_path":"gdrive:lecture/source.wav",
        }))
        self.srt = self.assets/"original_review.srt"
        self.srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n佛法\n\n"
                            "2\n00:00:01,100 --> 00:00:02,000\n修行\n")
        self.txt = self.assets/"review_transcript.txt"
        self.txt.write_text("佛法\n修行\n")
        self.qa = self.assets/"source_acoustic_triage.json"
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

    def processing_comparison(self, count=95):
        chirp_raw=json.dumps({'schema':'golden-v2-chirp-merged-v1',
            'job_id':self.job_id,'source_sha':self.sha,
            'timing_authority':'chirp_3','words':[]}).encode()
        chirp_sha=hashlib.sha256(chirp_raw).hexdigest()
        (self.assets/'chirp-merged.json').write_bytes(chirp_raw)
        comparison={"job_id":self.job_id,"source_sha256":self.sha,
                    "primary_evidence_sha256":"d"*64,
                    "chirp_evidence_sha256":chirp_sha,
                    "chirp_timing_authority":True,
                    "pending_review_count":count,
                    "candidates":[{"kind":"TEXT_DISAGREEMENT","index":i}
                                  for i in range(count)]}
        raw=json.dumps(comparison).encode()
        digest=hashlib.sha256(raw).hexdigest()
        (self.assets/"engine-comparison.json").write_bytes(raw)
        with sqlite3.connect(self.root/"ledger.sqlite") as db:
            db.execute("UPDATE v2_jobs SET status='PROCESSING' WHERE id=?",(self.job_id,))
            for name,sha in (("gemini_primary","d"*64),
                             ("chirp_independent",chirp_sha),("compare",digest)):
                db.execute("UPDATE v2_stages SET state='COMPLETE',evidence_sha=? "
                           "WHERE job_id=? AND name=?",(sha,self.job_id,name))
        return comparison

    def test_processing_proposal_carries_every_unresolved_comparison_candidate(self):
        comparison=self.processing_comparison()
        manifest=self.proposal()
        semantic=[item for item in manifest['open_items']
                  if item.get('disposition')=='SEMANTIC_REVIEW']
        self.assertEqual(len(semantic),95)
        self.assertEqual([item['candidate_index'] for item in semantic],list(range(95)))
        self.assertEqual([item['evidence'] for item in semantic],
                         comparison['candidates'])
        self.assertEqual(manifest['golden_job_status'],'PROCESSING')
        self.assertEqual(manifest['golden_qa_claim'],'NOT_CLAIMED')
        self.assertEqual(register_proposal(self.root,manifest)['state'],'PREPARED')
        self.assertEqual(self.ledger.status(self.job_id)['status'],'PROCESSING')

    def test_zero_acoustic_gaps_still_discloses_95_semantic_items(self):
        self.processing_comparison()
        self.qa.write_text(json.dumps({'job_id':self.job_id,
            'srt_sha256':hashlib.sha256(self.srt.read_bytes()).hexdigest(),
            'total_acoustic_gaps':0,'all_acoustic_gaps':[]}))
        manifest=self.proposal()
        self.assertEqual(manifest['acoustic_classification_counts'],{})
        self.assertEqual(len(manifest['open_items']),95)
        self.assertEqual(manifest['comparison_candidate_count'],95)

    def test_processing_rejects_stale_loose_chirp_without_bound_version(self):
        self.processing_comparison()
        canonical=self.assets/'chirp-merged.json'
        canonical.write_bytes(b'stale loose alias')
        with self.assertRaises(V2Conflict):self.proposal()
        bound_sha=self.ledger.status(self.job_id)['stages'][1]['evidence_sha']
        chirp_raw=json.dumps({'schema':'golden-v2-chirp-merged-v1',
            'job_id':self.job_id,'source_sha':self.sha,
            'timing_authority':'chirp_3','words':[]}).encode()
        (self.assets/f'chirp-merged-{bound_sha}.json').write_bytes(chirp_raw)
        manifest=self.proposal()
        self.assertEqual(manifest['chirp_merged_sha256'],bound_sha)
        self.assertEqual(manifest['comparison_candidate_count'],95)

    def test_processing_rejects_budget_exposure_above_approval(self):
        self.processing_comparison()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute("UPDATE v2_chirp_operations SET actual_cents=101 "
                       "WHERE job_id=?",(self.job_id,))
        with self.assertRaises(V2Conflict):self.proposal()

    def test_processing_rejects_missing_or_forged_comparison(self):
        self.processing_comparison()
        path=self.assets/'engine-comparison.json'
        path.unlink()
        with self.assertRaises(V2Conflict):self.proposal()
        self.processing_comparison()
        comparison=json.loads(path.read_text())
        comparison['primary_evidence_sha256']='f'*64
        path.write_text(json.dumps(comparison))
        with self.assertRaises(V2Conflict):self.proposal()

    def test_processing_rejects_active_stage_and_unknown_operation(self):
        self.processing_comparison()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute("UPDATE v2_stages SET state='RUNNING' WHERE job_id=? "
                       "AND name='adjudication'",(self.job_id,))
        with self.assertRaises(V2Conflict):self.proposal()
        with sqlite3.connect(self.root/'ledger.sqlite') as db:
            db.execute("UPDATE v2_stages SET state='PENDING' WHERE job_id=? "
                       "AND name='adjudication'",(self.job_id,))
            db.execute("UPDATE v2_chirp_operations SET status='OUTCOME_UNKNOWN' "
                       "WHERE job_id=?",(self.job_id,))
        with self.assertRaises(V2Conflict):self.proposal()

    def test_processing_registration_and_finalization_keep_golden_pending(self):
        self.processing_comparison()
        manifest=self.proposal()
        register_proposal(self.root,manifest)
        grant={'job_id':self.job_id,'manifest_sha256':manifest['manifest_sha256'],
               'quality_tier':TIER,'source_sha256':self.sha,
               'output_names':manifest['output_names'],'approve_delivery':True,
               'actor':'owner','reference':'approval'}
        remote=[{'name':name,'id':f'id{i}'} for i,name in enumerate(self.names)]
        receipt=finalize_readback(root=self.root,manifest=manifest,remote_files=remote,
            owner_grant=grant,signed_grant_verifier=lambda _:True,
            readback_verifier=lambda *_:True)
        self.assertEqual(receipt['golden_status_unchanged'],'PROCESSING')
        self.assertEqual([s['state'] for s in self.ledger.status(self.job_id)['stages'][-2:]],
                         ['PENDING','PENDING'])

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
