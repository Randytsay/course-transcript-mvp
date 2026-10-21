"""Content-conservation regressions for cross-window matching."""
import unittest
from app.pipeline.golden_v2_chirp_window_repair import merge_base_windows

class CrossWindowMultiplicityTests(unittest.TestCase):
    def test_monotonic_guard_records_inferred_boundary_changes(self):
        from app.pipeline.golden_v2_chirp_window_repair import _enforce_monotonic
        words=[{'word':'甲','start_ms':1000,'end_ms':1100,'_orig_index':0},
               {'word':'乙','start_ms':1050,'end_ms':1080,'_orig_index':1}]
        notes=_enforce_monotonic(words,0,5000)
        self.assertEqual(len(notes),1)
        self.assertEqual(notes[0].orig_index,1)
        self.assertEqual(notes[0].original_start,1050)
        self.assertEqual(notes[0].start_ms,1100)

    def test_merge_audit_records_inferred_overlap_boundary(self):
        windows=[{'start_ms':0,'end_ms':2000,'words':[
            {'word':'甲','start_ms':1000,'end_ms':1100},
            {'word':'乙','start_ms':1050,'end_ms':1200}]}]
        audit=[]
        merged=merge_base_windows(windows,2000,audit=audit)
        self.assertEqual(merged[1]['start_ms'],1100)
        self.assertEqual(audit[-1]['original_start_ms'],1050)
        self.assertEqual(audit[-1]['start_ms'],1100)
        self.assertFalse(audit[-1]['acoustic_timing_verified'])

    def test_cross_window_duplicate_matching_is_one_to_one(self):
        windows=[
            {'start_ms':0,'end_ms':2000,'words':[{'word':'那','start_ms':1000,'end_ms':1040}]},
            {'start_ms':900,'end_ms':2900,'words':[{'word':'那','start_ms':1010,'end_ms':1050},{'word':'那','start_ms':1120,'end_ms':1160}]},
        ]
        merged=merge_base_windows(windows,3000)
        self.assertEqual([w['word'] for w in merged],['那','那'])

class RepeatedRepairRetentionTests(unittest.TestCase):
    def test_rebuilding_base_replays_earlier_completed_repairs(self):
        import hashlib,json
        import test_golden_v2_repair_merge_baseline as baseline
        fixture=baseline.CompletedChirpRepairMergeTests()
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        ledger=fixture.ledger;golden=fixture.golden
        raw=baseline.wire({'results':[{'alternatives':[{'words':[
            {'word':'佛','startOffset':'1.0s','endOffset':'1.2s'},
            {'word':'法','startOffset':'4.0s','endOffset':'4.2s'},
        ]}]}]})
        raw_sha=hashlib.sha256(raw).hexdigest()
        folder=golden/'chirp-evidence/chunk-0000'
        (folder/'provider-raw.json').write_bytes(raw)
        meta=json.loads((folder/'window.json').read_bytes());meta['raw_sha']=raw_sha
        (folder/'window.json').write_bytes(baseline.wire(meta))
        prior=json.loads((golden/'chirp-merged.json').read_bytes())
        prior['window_result_shas']=[raw_sha]
        prior_bytes=baseline.wire(prior)
        (golden/'chirp-merged.json').write_bytes(prior_bytes)
        fixture.prior_sha=hashlib.sha256(prior_bytes).hexdigest()
        with ledger._tx() as db:
            db.execute('UPDATE v2_chirp_operations SET result_sha=? WHERE job_id=?',(raw_sha,fixture.job))
            db.execute("UPDATE v2_stages SET evidence_sha=? WHERE job_id=? AND name='chirp_independent'",(fixture.prior_sha,fixture.job))
        fixture.resume()
        def add_repair(wid,word,start,end,target,request):
            raw=baseline.wire({'results':[{'alternatives':[{'words':[
                {'word':word,'startOffset':'0.1s','endOffset':'0.3s'}
            ]}]}]})
            fixture.add_record(wid,start,end,'REPAIR',raw,1,request_id=request*64)
            with ledger._tx() as db:
                db.execute('UPDATE v2_chirp_operations SET target_intervals_json=? WHERE job_id=? AND window_id=?',(json.dumps([target]),fixture.job,wid))
        add_repair('repair-r01-00','僧',1900,2300,{'start_ms':2000,'end_ms':2200},'4')
        first=baseline.finish_approved_chirp(ledger=ledger,job_id=fixture.job,job_dir=fixture.job_dir,acoustic_inspector=fixture.passing_inspector)
        self.assertEqual([w['word'] for w in first['words']],['佛','僧','法'])
        self.assertEqual(first['timing_normalization_evidence']['chunk-0000']['raw_sha'],raw_sha)
        old_chirp=ledger.status(fixture.job)['stages'][1]['evidence_sha']
        for name,sha in [('compare','5'),('adjudication','6')]:
            claim=ledger.claim_next(fixture.job,worker_id='test',expected_stage=name)
            extra={'comparison_has_dispute':False} if name=='compare' else {}
            ledger.complete(claim,evidence_sha=sha*64,actual_cents=0,**extra)
        claim=ledger.claim_next(fixture.job,worker_id='test',expected_stage='golden_qa')
        ledger.hold_for_review(claim,evidence_sha='7'*64)
        ledger.resume_chirp_review_for_repair(fixture.job,revision=ledger.status(fixture.job)['revision'],source_sha=fixture.source_sha,previous_chirp_evidence_sha=old_chirp,previous_qa_evidence_sha='7'*64,transfer_cents=5,reviewer='unit-test',review_reference='second-round',worker_id='test',aggregate_cap_cents=1000,evidence_verifier=lambda **_:True)
        add_repair('repair-r02-00','新',2900,3300,{'start_ms':3000,'end_ms':3200},'8')
        result=baseline.finish_approved_chirp(ledger=ledger,job_id=fixture.job,job_dir=fixture.job_dir,acoustic_inspector=fixture.passing_inspector)
        self.assertEqual([w['word'] for w in result['words']],['佛','僧','新','法'])
        self.assertEqual(result['actual_cents'],32)

if __name__=='__main__':unittest.main()
