"""Synthetic regression tests for physical acoustic evidence and strict JSON."""
import json
import unittest
import hashlib
import subprocess
import tempfile
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from app.providers.golden_v2_auto_acceptance import _nonspeech_probes_valid, evaluate_auto_acceptance
from app.pipeline.golden_v2_auto_closeout import _wire
from app.providers.golden_v2_auto_gap_triage import inspect_subtitle_gaps, load_bound_triage
from tests.test_golden_v2_acoustic_adversarial_regressions import AcousticPrepareRegressionTests


def vad():
    return {'engine':'webrtcvad','sample_rate_hz':16000,'frame_ms':30,'frame_count':133,
            'mode0_speech_ratio':0.0752,'mode3_speech_ratio':0.0301,'rms_dbfs':-42.0,
            'thresholds':{'mode0_max':0.15,'mode3_max':0.08,'rms_max_dbfs':-35.0,
                          'mode0_speech_min':0.45,'mode3_speech_min':0.20,'rms_speech_min_dbfs':-35.0},
            'robust_non_speech':True,'robust_speech':False}


def gap():
    return {'index':0,'start_ms':1000,'end_ms':5000,'disposition':'NONSPEECH_CONFIRMED',
            'reason':'local_segmented_audio_evidence','slice_count':1,
            'probes':[{'start_ms':1000,'end_ms':5000,'disposition':'NONSPEECH_CONFIRMED',
                       'max_volume_dbfs':-10.0,'vad':vad()}]}


class EvidenceTests(unittest.TestCase):
    def test_measurements_not_boolean_determine_vad_proof(self):
        item=gap(); v=item['probes'][0]['vad']
        v.update(mode0_speech_ratio=1.0,mode3_speech_ratio=1.0,rms_dbfs=0.0)
        self.assertFalse(_nonspeech_probes_valid(item,1000,5000))

    def test_legitimate_producer_shaped_vad(self):
        self.assertTrue(_nonspeech_probes_valid(gap(),1000,5000))

    def test_contradictory_fields_and_incomplete_producer_evidence(self):
        mutations = [
            lambda x: x.update(reason='audio_character_uncertain'),
            lambda x: x.update(slice_count=2),
            lambda x: x['probes'][0]['vad']['thresholds'].update(mode0_max=1.1),
            lambda x: x['probes'][0]['vad'].pop('frame_count'),
            lambda x: x['probes'][0]['vad'].update(mode0_speech_ratio=.1),
            lambda x: x['probes'][0].update(max_volume_dbfs=-100),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                item=gap(); mutate(item)
                self.assertFalse(_nonspeech_probes_valid(item,1000,5000))

    def test_false_vad_claim_and_probe_reason_cannot_contradict_measurements(self):
        for mutation in ('false_non_speech','missing_speech_threshold','uncertain_probe'):
            with self.subTest(mutation=mutation):
                item=gap(); probe=item['probes'][0]
                if mutation=='false_non_speech':
                    probe['max_volume_dbfs']=-65
                    probe['vad']['robust_non_speech']=False
                    probe['vad']['rms_dbfs']=-70
                elif mutation=='missing_speech_threshold':
                    probe['vad']['thresholds'].pop('mode0_speech_min')
                else:
                    probe['reason']='audio_character_uncertain'
                self.assertFalse(_nonspeech_probes_valid(item,1000,5000))

    def test_real_synthetic_audio_producer_sidecar_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'golden-v2').mkdir()
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
                            'sine=frequency=440:sample_rate=16000:duration=6',
                            '-af','volume=0.01','-y',str(root/'normalized.flac')],check=True)
            source=hashlib.sha256((root/'normalized.flac').read_bytes()).hexdigest()
            srt=b'1\n00:00:00,000 --> 00:00:01,000\nHi\n\n2\n00:00:05,000 --> 00:00:06,000\nBye\n'
            report=inspect_subtitle_gaps(job_dir=root,source_sha256=source,duration_ms=6000,srt_bytes=srt)
            self.assertEqual(report['gaps'][0]['disposition'],'NONSPEECH_CONFIRMED')
            self.assertIsInstance(report['gaps'][0]['probes'][0]['vad'],dict)
            path=root/'golden-v2'/('auto-gap-triage-'+report['srt_sha256'][:20]+'.json')
            path.write_bytes(_wire(report))
            loaded=load_bound_triage(job_dir=root,source_sha256=source,srt_bytes=srt,duration_ms=6000)
            self.assertEqual(loaded['nonspeech_confirmed_count'],1)

    def test_triage_generation_never_emits_contradictory_non_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'normalized.flac').write_bytes(b'synthetic')
            source=hashlib.sha256(b'synthetic').hexdigest()
            srt=b'1\n00:00:00,000 --> 00:00:01,000\nHi\n\n2\n00:00:05,000 --> 00:00:06,000\nBye\n'
            probe=deepcopy(gap()['probes'][0]); probe['vad']['mode0_speech_ratio']=1.0
            with patch('app.providers.golden_v2_auto_gap_triage._probe_nonspeech',return_value=probe):
                report=inspect_subtitle_gaps(job_dir=root,source_sha256=source,duration_ms=6000,srt_bytes=srt)
            self.assertEqual(report['gaps'][0]['disposition'],'REVIEW')
            self.assertEqual(report['nonspeech_confirmed_count'],0)

    def test_direct_evaluator_rejects_bad_indexes(self):
        for index in [float('nan'),float('inf'),-1,True,'0',0.0]:
            with self.subTest(index=index):
                item=gap(); item['index']=index
                result=evaluate_auto_acceptance(job_id='v2p_'+'a'*32,source_sha256='a'*64,
                    source_audio_sha_verified=True,source_duration_ms=6000,
                    srt_bytes=b'1\n00:00:00,000 --> 00:00:01,000\nHi\n\n2\n00:00:05,000 --> 00:00:06,000\nBye\n',
                    txt_bytes=b'Hi\nBye\n',reference_txt_bytes=None,original_srt_bytes=None,
                    acoustic_review_items=[item],chirp_stage='COMPLETE')
                self.assertEqual(result['status'],'AUTO_BLOCKED_TECHNICAL')
                _wire(result)

    def test_artifact_wire_rejects_nonfinite(self):
        for n in [float('nan'),float('inf'),-float('inf')]:
            with self.subTest(n=n), self.assertRaises(ValueError): _wire({'nested':[n]})

    def test_triage_cli_will_not_create_nonfinite_artifact(self):
        from scripts.golden_v2_auto_gap_triage import main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'golden-v2').mkdir(); (root/'input.srt').write_bytes(b'x')
            report={'srt_sha256':'a'*64,'nested':float('nan'),'total_gaps':0,
                    'nonspeech_confirmed_count':0,'speech_confirmed_count':0,'review_count':0}
            with patch('sys.argv',['triage','--job-dir',str(root),'--source-sha','a'*64,
                                   '--srt',str(root/'input.srt')]), \
                 patch('scripts.golden_v2_auto_gap_triage.subprocess.run',return_value=SimpleNamespace(stdout='6')), \
                 patch('scripts.golden_v2_auto_gap_triage.inspect_subtitle_gaps',return_value=report):
                with self.assertRaises(ValueError):
                    main()
            self.assertFalse(list((root/'golden-v2').iterdir()))

    def test_acceptance_cli_rejects_nonfinite_input_and_output(self):
        from scripts.golden_v2_auto_acceptance import main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('srt','txt','audio'): (root/name).write_bytes(b'x')
            review=root/'review.json'
            review.write_text(json.dumps({'job_id':'v2p_'+'a'*32,'source_sha256':'a'*64,
                                          'unused':float('nan'),'review_items':[]}))
            argv=['accept','--job-id','v2p_'+'a'*32,'--source-sha','a'*64,
                  '--audio',str(root/'audio'),'--srt',str(root/'srt'),'--txt',str(root/'txt'),
                  '--title','x','--output-json',str(root/'out.json'),
                  '--output-report',str(root/'out.md'),'--acoustic-review-json',str(review)]
            with patch('sys.argv',argv), \
                 patch('scripts.golden_v2_auto_acceptance._sha_stream',return_value='a'*64), \
                 patch('scripts.golden_v2_auto_acceptance.subprocess.run',return_value=SimpleNamespace(stdout='6')):
                with self.assertRaises(ValueError): main()
                self.assertFalse((root/'out.json').exists())
                review.write_text(json.dumps({'job_id':'v2p_'+'a'*32,'source_sha256':'a'*64,'review_items':[]}))
                with patch('scripts.golden_v2_auto_acceptance.evaluate_auto_acceptance',return_value={'nested':float('inf')}):
                    with self.assertRaises(ValueError): main()
                self.assertFalse((root/'out.json').exists())


class PrepareHardeningTests(unittest.TestCase):
    def setUp(self):
        self.f=AcousticPrepareRegressionTests('test_malformed_vad_claim_cannot_be_used_as_proof')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_route_rejects_contradictory_vad(self):
        item=gap(); item['probes'][0]['vad'].update(mode0_speech_ratio=1.,mode3_speech_ratio=1.,rms_dbfs=0.)
        self.f.sidecar(gap=item)
        self.assertEqual(self.f.prepare().status_code,409)

    def test_route_rejects_budget_reason_as_proof(self):
        item=gap(); item['reason']='triage_slice_budget_exceeded'
        self.f.sidecar(gap=item)
        self.assertEqual(self.f.prepare().status_code,409)

    def test_route_rejects_nonfinite_json_and_invalid_indexes(self):
        for index in [float('nan'),float('inf'),-float('inf'),-1,True,'0',0.0,3]:
            with self.subTest(index=index):
                item=gap(); item.update(index=index,disposition='REVIEW')
                self.f.sidecar(gap=item)
                self.assertEqual(self.f.prepare().status_code,409)

    def test_route_rejects_nonfinite_unused_nested_fields_and_overflow(self):
        item=gap(); self.f.sidecar(gap=item)
        path=next(self.f.folder.glob('auto-gap-triage-*.json'))
        original=path.read_bytes()
        for token in (b'NaN', b'Infinity', b'-Infinity', b'1e9999'):
            with self.subTest(token=token):
                path.write_bytes(original.replace(b'"gaps": [',b'"unused": {"x": '+token+b'}, "gaps": ['))
                self.assertEqual(self.f.prepare().status_code,409)

    def test_route_accepts_complete_vad_sidecar(self):
        self.f.sidecar(gap=gap())
        response=self.f.prepare()
        self.assertEqual(response.status_code,200,response.text)
        report,_=self.f.reports()
        self.assertEqual(report['acoustic_nonspeech_confirmed_count'],1)
        json.loads(_wire(report),parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
