"""Draft may advance held Chirp evidence without pretending it passed QA."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest

from app.jobs.golden_v2_ledger import V2Conflict, GoldenV2Ledger
from app.pipeline.golden_v2_ai_draft import prepare_ai_draft
from app.pipeline.golden_v2_quote_intake import wire
from tests.test_golden_v2_delivery import V2DeliveryTests


class AIDraftTests(unittest.TestCase):
    def setUp(self):
        base=V2DeliveryTests()
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.root=base.root
        self.job_id=base.job_id
        self.folder=base.folder
        self.db=self.root/"ledger.sqlite"
        self.row_sha="9"*64
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE v2_jobs SET status='PROCESSING' WHERE id=?", (self.job_id,))
            db.execute("UPDATE v2_stages SET state='RUNNING',evidence_sha=NULL WHERE "
                       "job_id=? AND name='chirp_independent'",(self.job_id,))
            db.execute(
                "INSERT INTO v2_chirp_operations "
                "(job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,"
                "status,operation_name,request_id,output_uri,estimated_cents,"
                "purpose,result_sha,actual_cents,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.job_id,"chunk-0000","a"*64,"b"*64,"gs://test/source",
                 0,1000,"DONE","projects/test/operations/1","c"*64,"gs://test/out",
                 1,"BASE",self.row_sha,1,"2026-10-24T00:00:00+00:00"))
        candidate=dict(schema="golden-v2-chirp-merged-v1",job_id=self.job_id,
                       source_sha="a"*64,timing_authority="chirp_3",
                       golden_pass=False,acoustic_timing_verified=False,
                       window_result_shas=[self.row_sha],
                       duration_ms=1000,
                       words=[dict(word="測試",start_ms=0,end_ms=1000)])
        self.csha=hashlib.sha256(wire(candidate).encode()).hexdigest()
        (self.folder/f"chirp-merged-candidate-{self.csha[:20]}.json").write_text(wire(candidate))
        acoustic=dict(status="REVIEW",source_sha256="a"*64,merged_sha256=self.csha)
        self.asha=hashlib.sha256(wire(acoustic).encode()).hexdigest()
        (self.folder/f"chirp-acoustic-qa-{self.asha[:20]}.json").write_text(wire(acoustic))
        self.revision=GoldenV2Ledger.open_existing(self.db).status(self.job_id)["revision"]

    def draft(self,**overrides):
        args=dict(root=self.root,job_id=self.job_id,
                  candidate_sha256=self.csha,acoustic_sha256=self.asha,
                  expected_revision=self.revision)
        args.update(overrides)
        return prepare_ai_draft(**args)

    def test_source_bound_draft_has_no_publish_grant_or_false_qa(self):
        result=self.draft()
        self.assertEqual(result["quality_tier"],"AI_DRAFT_REVIEW_REQUIRED")
        self.assertFalse(result["publication_allowed"])
        self.assertFalse(result["provider_calls_started"])
        self.assertFalse(result["drive_mutations_started"])
        self.assertEqual(result["cue_count"],1)
        self.assertEqual(self.draft(),result)
        manifest=json.loads((self.folder/"ai-draft-manifest.json").read_text())
        self.assertEqual(manifest["acoustic_gate"],"REVIEW")
        self.assertEqual(manifest["semantic_gate"],"REVIEW")
        self.assertFalse((self.root/"jobs"/self.job_id/"owner-ai-publish-grant.json").exists())

    def test_source_or_sha_mismatch_refused_before_any_output(self):
        for args in (dict(candidate_sha256="e"*64),
                     dict(acoustic_sha256="f"*64),
                     dict(expected_revision=self.revision-1)):
            with self.subTest(args):
                with self.assertRaises(V2Conflict):
                    self.draft(**args)
                self.assertFalse((self.folder/"ai-draft.srt").exists())

    def test_uncertain_chirp_operation_never_renders(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE v2_chirp_operations SET status='SUBMITTING' "
                       "WHERE job_id=?",(self.job_id,))
        with self.assertRaises(V2Conflict):
            self.draft()
        self.assertFalse((self.folder/"ai-draft.srt").exists())


if __name__=="__main__":
    unittest.main()
