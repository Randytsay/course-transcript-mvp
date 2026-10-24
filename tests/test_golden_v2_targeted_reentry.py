import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_targeted_reentry import (
    reserve_targeted_repair_and_reenter,verify_persisted_owner_targeted_grant,
    recover_interrupted_targeted_claim,
)


class CappedReentryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"ledger.sqlite"
        self.ledger = GoldenV2Ledger(self.path)
        self.sources = {'v2p_'+format(i,'032x'):format(i+10,'064x') for i in range(4)}
        self.ids = list(self.sources)
        self.ref = 'owner-signed-20261024-four-source-standard-repair-100c'
        for index,(job,sha) in enumerate(self.sources.items()):
            stages = [('gemini_primary','COMPLETE',80,3,None),
                      ('chirp_independent','REVIEW_REQUIRED',20,10,'e'*64),
                      ('compare','PENDING',0,0,None),('third_optional','SKIPPED',0,0,None),
                      ('adjudication','PENDING',0,0,None),('golden_qa','PENDING',0,0,None)]
            with self.ledger._tx() as db:
                db.execute("INSERT INTO v2_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                           (job,sha,'FLASH_CHIRP',index, 'original-owner-grant',100,100,
                            'NEEDS_REVIEW','2026-10-20T00:00:00+00:00'))
                for ord,(name,state,est,actual,evidence) in enumerate(stages):
                    db.execute(
                        "INSERT INTO v2_stages VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (job,ord,name,state,est,actual,None,evidence,None,
                         '2026-10-20T00:00:00+00:00'))
                db.execute("INSERT INTO v2_chirp_operations "
                           "(job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,end_ms,"
                           "status,operation_name,request_id,output_uri,estimated_cents,"
                           "purpose,result_sha,actual_cents,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (job,'chunk-0000',sha,'b'*64,'gs://bucket/source',0,10000,
                            'DONE','projects/test/operations/'+str(index),
                            'c'*64,'gs://bucket/result/',10,'BASE','d'*64,10,
                            '2026-10-20T00:00:00+00:00'))
        self.verified = 0

    def reenter(self, index=0, cents=12, **kw):
        self.verified += 1
        args=dict(
            ledger=self.ledger,job_id=self.ids[index],
            expected_revision=index,expected_source_sha=self.sources[self.ids[index]],
            assessment_sha256='a'*64,source_scopes=self.sources,
            batch_reference=self.ref,batch_authorized_cents=100,
            requested_cents=cents,worker_id='isolated-owner-rescue',
            actor='owner-authenticated',
            trusted_owner_grant_verifier=lambda **v: True,
            evidence_verifier=lambda **v: True,
            aggregate_cap_cents=1000)
        args.update(kw)
        return reserve_targeted_repair_and_reenter(**args)

    def test_budgeted_claim_rotation_preserves_existing_operations(self):
        before=self.ledger.chirp_windows(self.ids[0])
        result=self.reenter()
        self.assertEqual(result['stage'],'chirp_independent')
        self.assertNotEqual(result['claim_hash'] if 'claim_hash' in result else 'no_hash',
                            result['claim'])
        self.ledger.assert_stage_claim(result,stage='chirp_independent')
        self.assertEqual(self.ledger.chirp_windows(self.ids[0]),before)
        st=self.ledger.status(self.ids[0])
        self.assertEqual(st['revision'],1)
        self.assertEqual(st['status'],'PROCESSING')
        self.assertEqual(next(s for s in st['stages'] if s['name']=='chirp_independent')['state'],'RUNNING')
        self.assertEqual(next(s for s in st['stages'] if s['name']=='gemini_primary')['estimate_cents'],68)
        with self.assertRaises(V2Conflict):
            self.reenter()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT SUM(reserved_cents) FROM v2_targeted_repair_claims").fetchone()[0],12)

    def test_unverified_human_owner_grant_never_claims(self):
        before=self.ledger.status(self.ids[0])
        with self.assertRaises(V2Conflict):
            self.reenter(trusted_owner_grant_verifier=lambda **v: False)
        self.assertEqual(self.ledger.status(self.ids[0]),before)

    def test_unknown_operation_and_wrong_sha_block_claim(self):
        with self.assertRaises(V2Conflict):
            self.reenter(expected_source_sha='f'*64)
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_chirp_operations SET status='SUBMITTING' WHERE job_id=?",(self.ids[1],))
        with self.assertRaises(V2Conflict):
            self.reenter()
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_chirp_operations SET status='DONE' WHERE job_id=?",(self.ids[1],))

    def test_owner_ceiling_and_local_stale_requirements(self):
        with self.assertRaises(V2Conflict):
            self.reenter(batch_authorized_cents=101)
        with self.assertRaises(V2Conflict):
            self.reenter(requested_cents=101)
        with self.assertRaises(V2Conflict):
            self.reenter(aggregate_cap_cents=1)
        with self.assertRaises(V2Conflict):
            self.reenter(evidence_verifier=lambda **v: False)

    def test_safe_stale_reissue_preserves_reserved_budget(self):
        granted=self.reenter()
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_stages SET updated_at=? WHERE job_id=? AND name='chirp_independent'",
                       ('2026-10-20T00:00:00+00:00',self.ids[0]))
        repaired=recover_interrupted_targeted_claim(
            ledger=self.ledger,job_id=self.ids[0],scope_sha=granted['scope_sha'],
            expected_revision=1)
        self.ledger.assert_stage_claim(repaired,stage='chirp_independent')
        with self.assertRaises(V2Conflict):
            self.ledger.assert_stage_claim(granted,stage='chirp_independent')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT SUM(reserved_cents) FROM v2_targeted_repair_claims").fetchone()[0],12)

    def test_submitting_never_reissues_even_with_stale_claim(self):
        granted=self.reenter()
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_stages SET updated_at=? WHERE job_id=? AND name='chirp_independent'",
                       ('2026-10-20T00:00:00+00:00',self.ids[0]))
            db.execute("UPDATE v2_chirp_operations SET status='SUBMITTING' WHERE job_id=?",(self.ids[0],))
        with self.assertRaises(V2Conflict):
            recover_interrupted_targeted_claim(
                ledger=self.ledger,job_id=self.ids[0],scope_sha=granted['scope_sha'],
                expected_revision=1)

    def test_live_paid_worker_cannot_be_stolen(self):
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_jobs SET status='PROCESSING' WHERE id=?",(self.ids[0],))
            db.execute("UPDATE v2_stages SET state='RUNNING',updated_at=? WHERE job_id=? AND name='chirp_independent'",
                       (datetime.now(timezone.utc).isoformat(),self.ids[0]))
        with self.assertRaises(V2Conflict):
            self.reenter()

    def test_renaming_grant_after_one_reservation_never_extends_scope(self):
        self.reenter(index=0,cents=13)
        with self.assertRaises(V2Conflict):
            self.reenter(index=1,cents=13,source_scopes={**self.sources,self.ids[0]:'f'*64})
        # Cannot claim another source concurrently with the first paid stage
        # RUNNING; once the first claim is settled, the existing signed grant
        # can reserve the next source, always under the same global cap.
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_stages SET state='COMPLETE' WHERE job_id=? AND name='chirp_independent'",(self.ids[0],))
        second=self.reenter(index=1,cents=13)
        self.assertEqual(second['new_reserved_cents'],13)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT SUM(reserved_cents) FROM v2_targeted_repair_claims").fetchone()[0],26)

    def test_persisted_signed_grant_expires_and_rejects_widening(self):
        import os
        from unittest.mock import patch
        from app.pipeline.golden_v2_runtime_services import signed_grant
        from app.pipeline.golden_v2_quote_intake import wire
        from app.pipeline.golden_v2_targeted_reentry import _hash
        scope_hash = _hash({"ref":self.ref, "sources":self.sources,
                            "max_cents":100,"stage":"chirp_3_standard_targeted_repair"})
        body=dict(schema="golden-v2-four-source-repair-budget-v1",
                  reference=self.ref,actor="owner-authenticated",
                  source_scopes=self.sources,authorized_cents=100,
                  scope_sha=scope_hash,model="chirp_3_standard_targeted_repair",
                  expires_at="2099-01-01T00:00:00+00:00",
                  allow_base_replay=False,allow_production_deploy=False)
        path=Path(self.tmp.name)/"owner-grant.json"
        with patch.dict(os.environ,{"COURSE_TRANSCRIPT_V2_GRANT_SECRET":"X"*64}):
            path.write_text(wire(signed_grant(body)))
            kw=dict(grant_path=path,reference=self.ref,actor="owner-authenticated",
                    source_scopes=self.sources,authorized_cents=100,scope_sha=scope_hash)
            self.assertTrue(verify_persisted_owner_targeted_grant(**kw))
            with self.assertRaises(V2Conflict):
                verify_persisted_owner_targeted_grant(**{**kw,"authorized_cents":101})
            body["expires_at"]="2026-01-01T00:00:00+00:00"
            path.write_text(wire(signed_grant(body)))
            with self.assertRaises(V2Conflict):
                verify_persisted_owner_targeted_grant(**kw)

    def test_four_source_100_cent_aggregate_cap(self):
        self.reenter(index=0,cents=30)
        for n,cents in [(1,30),(2,30)]:
            with self.ledger._tx() as db:
                db.execute("UPDATE v2_stages SET state='COMPLETE',actual_cents=10 WHERE job_id=? AND name='chirp_independent'",(self.ids[n-1],))
            self.reenter(index=n,cents=cents)
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_stages SET state='COMPLETE',actual_cents=10 WHERE job_id=? AND name='chirp_independent'",(self.ids[2],))
        with self.assertRaises(V2Conflict):
            self.reenter(index=3,cents=11)
        self.reenter(index=3,cents=10)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT SUM(reserved_cents) FROM v2_targeted_repair_claims").fetchone()[0],100)

    def test_original_claim_not_reused_for_old_running_stage(self):
        j=self.ids[0]
        with self.ledger._tx() as db:
            db.execute("UPDATE v2_jobs SET status='PROCESSING' WHERE id=?",(j,))
            db.execute("UPDATE v2_stages SET state='RUNNING',claim_hash=?,updated_at=? WHERE job_id=? AND name='chirp_independent'",
                       ('f'*64,'2026-10-20T00:00:00+00:00',j))
        result=self.reenter()
        with sqlite3.connect(self.path) as db:
            prior=db.execute("SELECT previous_claim_hash FROM v2_targeted_repair_claims WHERE job_id=?",(j,)).fetchone()[0]
        self.assertEqual(prior,'f'*64)
        self.assertNotEqual(hashlib.sha256(result['claim'].encode()).hexdigest(),prior)


if __name__=='__main__':
    unittest.main()
