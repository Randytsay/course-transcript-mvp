"""Bounded local silence-vs-speech classification, no paid provider."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_auto_gap_triage import inspect_subtitle_gaps, load_bound_triage
from app.providers.golden_v2_auto_acceptance import evaluate_auto_acceptance, render_human_review_advisory


class LocalGapTriageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.job=Path(tmp.name);(self.job/'golden-v2').mkdir()
        self.audio=b'test-only normalized audio'
        (self.job/'normalized.flac').write_bytes(self.audio)
        self.sha=hashlib.sha256(self.audio).hexdigest()
        self.srt=('1\n00:00:00,000 --> 00:00:02,000\n開始\n\n'
                  '2\n00:00:10,000 --> 00:00:12,000\n結束\n').encode()

    def _inspect(self, disposition):
        with patch('app.providers.golden_v2_auto_gap_triage._probe_nonspeech',
                   side_effect=lambda job,start,end:{'disposition':disposition,'start_ms':start,'end_ms':end,'max_volume_dbfs':-120.0}):
            return inspect_subtitle_gaps(job_dir=self.job,source_sha256=self.sha,
                        duration_ms=12000,srt_bytes=self.srt,slice_ms=3000)

    def test_blackboard_pause_classified_nonspeech_without_blocking(self):
        triage=self._inspect('NONSPEECH_CONFIRMED')
        self.assertEqual(triage['total_gaps'],1)
        self.assertEqual(triage['nonspeech_confirmed_count'],1)
        result=evaluate_auto_acceptance(job_id='v2p_'+'a'*32,source_sha256=self.sha,
            source_audio_sha_verified=True,source_duration_ms=12000,
            srt_bytes=self.srt,txt_bytes='開始\n結束\n'.encode(),
            reference_txt_bytes=None,original_srt_bytes=None,
            acoustic_review_items=triage['gaps'],chirp_stage='COMPLETE',
            external_provider_reconciled=True)
        self.assertTrue(result['can_close_without_human'])
        self.assertFalse(result['human_review_advisory']['recommended'])
        self.assertEqual(result['acoustic_nonspeech_confirmed_count'],1)
        report=render_human_review_advisory(result,lesson_title='寫黑板片段')
        self.assertIn('已有聲學非語音證據可略過：1',report)
        self.assertNotIn('00:00:02.000–00:00:10.000（8.0s）',report)
        self.assertFalse(result['formal_golden_pass'])

    def test_positive_speech_is_recommended_not_hallucinated(self):
        triage=self._inspect('SPEECH_CONFIRMED')
        self.assertEqual(triage['speech_confirmed_count'],1)
        self.assertFalse(triage['golden_qa_pass'])
        self.assertEqual(triage['gaps'][0]['start_ms'],2000)
        self.assertEqual(triage['gaps'][0]['end_ms'],10000)

    def test_mixed_slices_are_not_claimed_silent(self):
        state=iter(['NONSPEECH_CONFIRMED','REVIEW','NONSPEECH_CONFIRMED'])
        with patch('app.providers.golden_v2_auto_gap_triage._probe_nonspeech',
                   side_effect=lambda job,start,end:{'disposition':next(state),'start_ms':start,'end_ms':end,'max_volume_dbfs':-120.0}):
            triage=inspect_subtitle_gaps(job_dir=self.job,source_sha256=self.sha,
                        duration_ms=12000,srt_bytes=self.srt,slice_ms=3000)
        self.assertEqual(triage['review_count'],1)
        self.assertEqual(triage['nonspeech_confirmed_count'],0)

    def test_slice_budget_exceeded_is_review(self):
        triage=inspect_subtitle_gaps(job_dir=self.job,source_sha256=self.sha,
            duration_ms=12000,srt_bytes=self.srt,max_slices=1,slice_ms=3000)
        self.assertEqual(triage['gaps'][0]['disposition'],'REVIEW')

    def test_readback_rejects_wrong_srt_sha(self):
        triage=self._inspect('NONSPEECH_CONFIRMED')
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        self.assertEqual(load_bound_triage(job_dir=self.job,source_sha256=self.sha,
            srt_bytes=self.srt,duration_ms=12000)['total_gaps'],1)
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256='0'*64,
                srt_bytes=self.srt,duration_ms=12000)

    def test_partial_sidecar_exposes_unclassified_gaps(self):
        self.srt=('1\n00:00:00,000 --> 00:00:02,000\n開始\n\n'
                  '2\n00:00:10,000 --> 00:00:12,000\n中間\n\n'
                  '3\n00:00:20,000 --> 00:00:22,000\n結束\n').encode()
        triage=inspect_subtitle_gaps(job_dir=self.job,source_sha256=self.sha,
            duration_ms=22000,srt_bytes=self.srt,max_slices=1,slice_ms=3000)
        triage['gaps']=triage['gaps'][:1]
        triage['total_gaps']=1
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        loaded=load_bound_triage(job_dir=self.job,source_sha256=self.sha,
            srt_bytes=self.srt,duration_ms=22000)
        result=evaluate_auto_acceptance(job_id='v2p_'+'a'*32,source_sha256=self.sha,
            source_audio_sha_verified=True,source_duration_ms=22000,srt_bytes=self.srt,
            txt_bytes='開始\n中間\n結束\n'.encode(),reference_txt_bytes=None,
            original_srt_bytes=None,acoustic_review_items=loaded['gaps'],chirp_stage='COMPLETE')
        self.assertEqual(result['acoustic_triage_status'],'PARTIAL')
        self.assertEqual(result['acoustic_unclassified_gap_count'],1)
        self.assertEqual(result['acoustic_open_item_count'],2)
        self.assertFalse(result['formal_golden_pass'])

    def test_sidecar_with_wrong_gap_is_rejected_despite_self_consistent_count(self):
        triage=self._inspect('REVIEW')
        triage['gaps'][0]['start_ms']=3000
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_nonspeech_sidecar_rejects_backwards_probe_segments(self):
        triage=self._inspect('NONSPEECH_CONFIRMED')
        gap=triage['gaps'][0]
        gap['probes']=[{'start_ms':2000,'end_ms':5000,'disposition':'NONSPEECH_CONFIRMED','max_volume_dbfs':-120},
                       {'start_ms':5000,'end_ms':4000,'disposition':'NONSPEECH_CONFIRMED','max_volume_dbfs':-120},
                       {'start_ms':4000,'end_ms':10000,'disposition':'NONSPEECH_CONFIRMED','max_volume_dbfs':-120}]
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_duplicate_sidecar_gap_is_rejected(self):
        triage=self._inspect('REVIEW')
        triage['gaps'].append(dict(triage['gaps'][0]))
        triage['total_gaps']=2
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_missing_nonspeech_probes_are_rejected(self):
        triage=self._inspect('NONSPEECH_CONFIRMED')
        triage['gaps'][0]['probes']=[]
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_no_gap_sidecar_does_not_certify_acoustics(self):
        self.srt=('1\n00:00:00,000 --> 00:00:06,000\n開始\n\n'
                  '2\n00:00:06,000 --> 00:00:12,000\n結束\n').encode()
        triage=self._inspect('NONSPEECH_CONFIRMED')
        self.assertEqual(triage['gaps'],[])
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        loaded=load_bound_triage(job_dir=self.job,source_sha256=self.sha,
            srt_bytes=self.srt,duration_ms=12000)
        self.assertEqual(loaded['total_gaps'],0)
        self.assertFalse(loaded['golden_qa_pass'])
        result=evaluate_auto_acceptance(job_id='v2p_'+'a'*32,source_sha256=self.sha,
            source_audio_sha_verified=True,source_duration_ms=12000,srt_bytes=self.srt,
            txt_bytes='開始\n結束\n'.encode(),reference_txt_bytes=None,original_srt_bytes=None,
            acoustic_review_items=loaded['gaps'],chirp_stage='COMPLETE')
        self.assertEqual(result['acoustic_open_item_count'],0)
        self.assertFalse(result['formal_golden_pass'])
        self.assertFalse(result['audio_fully_listened'])

    def test_sidecar_rejects_conflicting_coordinate_aliases(self):
        triage=self._inspect('REVIEW')
        triage['gaps'][0].update(gap_start_ms=0,gap_end_ms=1000)
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_malformed_sidecars_raise_v2_conflict(self):
        triage=self._inspect('REVIEW')
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        for payload in [None, [], 'invalid', triage|{'gaps':None},
                        triage|{'gaps':42}, triage|{'gaps':{}}, triage|{'gaps':'x'}]:
            with self.subTest(payload=payload):
                path.write_text(json.dumps(payload))
                with self.assertRaises(V2Conflict):
                    load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                        srt_bytes=self.srt,duration_ms=12000)
        path.write_text('{')
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_short_gap_missing_from_partial_sidecar_stays_open(self):
        self.srt=('1\n00:00:00,000 --> 00:00:02,000\n開始\n\n'
                  '2\n00:00:04,000 --> 00:00:06,000\n結束\n').encode()
        with patch('app.providers.golden_v2_auto_gap_triage._probe_nonspeech',
                   side_effect=lambda job,start,end:{'disposition':'REVIEW','start_ms':start,'end_ms':end}):
            triage=inspect_subtitle_gaps(job_dir=self.job,source_sha256=self.sha,
                duration_ms=6000,srt_bytes=self.srt,min_gap_ms=1000)
        triage['gaps']=[];triage['total_gaps']=0
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        loaded=load_bound_triage(job_dir=self.job,source_sha256=self.sha,
            srt_bytes=self.srt,duration_ms=6000)
        result=evaluate_auto_acceptance(job_id='v2p_'+'a'*32,source_sha256=self.sha,
            source_audio_sha_verified=True,source_duration_ms=6000,srt_bytes=self.srt,
            txt_bytes='開始\n結束\n'.encode(),reference_txt_bytes=None,original_srt_bytes=None,
            acoustic_review_items=loaded['gaps'],chirp_stage='COMPLETE',external_provider_reconciled=True)
        self.assertEqual(loaded['inventory_missing_count'],1)
        self.assertEqual(result['subtitle_gaps_over_3s_count'],0)
        self.assertEqual(result['acoustic_triage_status'],'PARTIAL')
        self.assertEqual(result['acoustic_open_item_count'],1)
        self.assertTrue(result['human_review_advisory']['recommended'])

    def test_sidecar_rejects_conflicting_disposition_alias(self):
        triage=self._inspect('NONSPEECH_CONFIRMED')
        triage['gaps'][0]['acoustic_disposition']='SPEECH_CONFIRMED'
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_oversized_numeric_evidence_is_rejected_without_crashing(self):
        triage=self._inspect('NONSPEECH_CONFIRMED')
        triage['gaps'][0]['probes'][0]['max_volume_dbfs']=10**400
        path=self.job/'golden-v2'/f"auto-gap-triage-{triage['srt_sha256'][:20]}.json"
        path.write_text(json.dumps(triage))
        with self.assertRaises(V2Conflict):
            load_bound_triage(job_dir=self.job,source_sha256=self.sha,
                srt_bytes=self.srt,duration_ms=12000)

    def test_audio_sha_failure_is_technical_error(self):
        with self.assertRaises(V2Conflict):
            inspect_subtitle_gaps(job_dir=self.job,source_sha256='0'*64,
                duration_ms=12000,srt_bytes=self.srt)

if __name__=='__main__':unittest.main()
