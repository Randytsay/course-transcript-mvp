"""Auto acceptance can close with warnings without forging Golden PASS."""
import hashlib
import unittest
from app.providers.golden_v2_auto_acceptance import (
    evaluate_auto_acceptance, render_human_review_advisory, TIER)


class AutoAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.srt=('1\n00:00:01,000 --> 00:00:03,000\n清淨心\n\n'
                  '2\n00:00:07,000 --> 00:00:09,000\n菩提種子\n').encode()
        self.txt='清淨心\n菩提種子\n'.encode()
        self.params=dict(job_id='v2p_'+'a'*32, source_sha256='b'*64,
                         source_audio_sha_verified=True, source_duration_ms=10000,
                         srt_bytes=self.srt, txt_bytes=self.txt,
                         reference_txt_bytes='清淨心。菩提種子。'.encode(),
                         original_srt_bytes=self.srt,
                         acoustic_review_items=[{'index':1,'gap_start_ms':3000,
                                                 'gap_end_ms':7000,'disposition':'REVIEW'}],
                         chirp_stage='PROCESSING',
                         external_provider_reconciled=False,
                         source_revision_verified=False)

    def test_nonhuman_tier_closes_with_disclosed_open_items(self):
        x=evaluate_auto_acceptance(**self.params)
        self.assertEqual(x['status'],TIER)
        self.assertTrue(x['can_close_without_human'])
        self.assertFalse(x['formal_golden_pass'])
        self.assertFalse(x['audio_fully_listened'])
        self.assertEqual(len(x['acoustic_review_items']),1)
        self.assertEqual(x['subtitle_gaps_over_3s_count'],1)
        self.assertTrue(x['human_review_advisory']['recommended'])
        self.assertFalse(x['human_review_advisory']['mandatory_for_auto_closeout'])
        self.assertIn('00:00:03.000',render_human_review_advisory(x,lesson_title='測試'))
        self.assertEqual(x['artifact_shas']['srt_sha256'],hashlib.sha256(self.srt).hexdigest())

    def test_absent_triage_reports_unclassified_gap_without_claiming_pass(self):
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':None}))
        self.assertEqual(x['status'],TIER)
        self.assertFalse(x['formal_golden_pass'])
        self.assertEqual(x['acoustic_triage_status'],'NOT_RUN')
        self.assertIn('acoustic_unclassified_gap_count',x)
        self.assertEqual(x['acoustic_unclassified_gap_count'],1)
        self.assertEqual(x['acoustic_open_item_count'],1)
        report=render_human_review_advisory(x,lesson_title='測試')
        self.assertIn('未分類字幕空檔：1',report)
        self.assertIn('聲學待核對區間：1',report)

    def test_no_gap_does_not_imply_acoustic_triage_or_golden_pass(self):
        srt=('1\n00:00:00,000 --> 00:00:05,000\n清淨心\n\n'
             '2\n00:00:05,000 --> 00:00:10,000\n菩提種子\n').encode()
        x=evaluate_auto_acceptance(**(self.params|{'srt_bytes':srt,'acoustic_review_items':None}))
        self.assertEqual(x['status'],TIER)
        self.assertEqual(x['subtitle_gaps_over_3s_count'],0)
        self.assertEqual(x['acoustic_unclassified_gap_count'],0)
        self.assertEqual(x['acoustic_triage_status'],'NOT_RUN')
        self.assertFalse(x['formal_golden_pass'])
        self.assertIn('聲學分流狀態：NOT_RUN',render_human_review_advisory(x,lesson_title='測試'))

    def test_empty_triage_inventory_is_partial_when_a_gap_exists(self):
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':[]}))
        self.assertEqual(x['acoustic_triage_status'],'PARTIAL')
        self.assertEqual(x['acoustic_unclassified_gap_count'],1)
        self.assertEqual(x['acoustic_open_item_count'],1)
        self.assertFalse(x['formal_golden_pass'])

    def test_nonspeech_without_probe_evidence_remains_unclassified(self):
        items=[{'start_ms':3000,'end_ms':7000,'disposition':'NONSPEECH_CONFIRMED'}]
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':items}))
        self.assertEqual(x['status'],TIER)
        self.assertEqual(x['acoustic_nonspeech_confirmed_count'],0)
        self.assertEqual(x['acoustic_unclassified_gap_count'],1)
        self.assertEqual(x['acoustic_open_item_count'],1)
        self.assertFalse(x['formal_golden_pass'])

    def test_invalid_nonspeech_outside_subtitle_gaps_remains_unresolved(self):
        srt=('1\n00:00:00,000 --> 00:00:10,000\n清淨心\n菩提種子\n').encode()
        items=[{'start_ms':2000,'end_ms':2500,'disposition':'NONSPEECH_CONFIRMED'}]
        x=evaluate_auto_acceptance(**(self.params|{'srt_bytes':srt,'acoustic_review_items':items,
            'original_srt_bytes':None,'external_provider_reconciled':True}))
        self.assertEqual(x['acoustic_open_item_count'],1)
        self.assertEqual(x['acoustic_review_items'][0]['disposition'],'REVIEW')
        self.assertTrue(x['human_review_advisory']['recommended'])

    def test_evaluator_rejects_conflicting_coordinate_aliases(self):
        items=[{'start_ms':3000,'end_ms':7000,'gap_start_ms':2000,
                'gap_end_ms':7000,'disposition':'REVIEW'}]
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':items}))
        self.assertIn('invalid_acoustic_review_interval',x['technical_blockers'])
        self.assertEqual(x['acoustic_triage_status'],'PARTIAL')

    def test_duplicate_pending_intervals_are_not_double_counted(self):
        item={'start_ms':2000,'end_ms':2500,'disposition':'REVIEW'}
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':[item,item]}))
        self.assertEqual(len(x['acoustic_review_items']),1)
        self.assertEqual(x['acoustic_open_item_count'],2)  # one pending + one SRT gap

    def test_conflicting_evidence_keeps_gap_open_in_report(self):
        item={'start_ms':3000,'end_ms':7000,'disposition':'NONSPEECH_CONFIRMED',
              'probes':[{'start_ms':3000,'end_ms':7000,'disposition':'NONSPEECH_CONFIRMED','max_volume_dbfs':-100}]}
        speech={'start_ms':3000,'end_ms':7000,'disposition':'SPEECH_CONFIRMED'}
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':[item,speech],
             'external_provider_reconciled':True,'original_srt_bytes':None}))
        self.assertEqual(x['acoustic_nonspeech_confirmed_count'],0)
        self.assertEqual(x['acoustic_open_item_count'],1)
        self.assertTrue(x['human_review_advisory']['recommended'])
        self.assertIn('00:00:03.000–00:00:07.000（4.0s）',render_human_review_advisory(x,lesson_title='x'))

    def test_overlapping_pending_prevents_nonspeech_gap_dismissal(self):
        item={'start_ms':3000,'end_ms':7000,'disposition':'NONSPEECH_CONFIRMED',
              'probes':[{'start_ms':3000,'end_ms':7000,'disposition':'NONSPEECH_CONFIRMED','max_volume_dbfs':-100}]}
        speech={'start_ms':3500,'end_ms':4500,'disposition':'SPEECH_CONFIRMED'}
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':[item,speech]}))
        self.assertEqual(x['acoustic_nonspeech_confirmed_count'],0)
        self.assertIn('00:00:03.000–00:00:07.000（4.0s）',render_human_review_advisory(x,lesson_title='x'))

    def test_invalid_or_conflicting_disposition_retains_known_interval(self):
        for item in [
            {'start_ms':2000,'end_ms':2500,'disposition':'NOT_VALID'},
            {'start_ms':2000,'end_ms':2500,'disposition':'NONSPEECH_CONFIRMED',
             'acoustic_disposition':'SPEECH_CONFIRMED',
             'probes':[{'start_ms':2000,'end_ms':2500,'disposition':'NONSPEECH_CONFIRMED','max_volume_dbfs':-100}]}]:
            with self.subTest(item=item):
                x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':[item]}))
                self.assertEqual(x['acoustic_review_items'][0]['disposition'],'REVIEW')
                self.assertEqual(x['acoustic_nonspeech_confirmed_count'],0)
                self.assertIn('invalid_acoustic_disposition',x['technical_blockers'])

    def test_contradictory_probe_evidence_cannot_certify_nonspeech(self):
        item={'start_ms':3000,'end_ms':7000,'disposition':'NONSPEECH_CONFIRMED',
              'probes':[{'start_ms':3000,'end_ms':7000,'disposition':'NONSPEECH_CONFIRMED',
                         'max_volume_dbfs':-100,
                         'vad':{'robust_non_speech':True,'robust_speech':True}}]}
        x=evaluate_auto_acceptance(**(self.params|{'acoustic_review_items':[item]}))
        self.assertEqual(x['acoustic_nonspeech_confirmed_count'],0)
        self.assertEqual(x['acoustic_review_items'][0]['disposition'],'REVIEW')

    def test_natural_paragraph_transcript_matches_source_cues(self):
        # Subtitle line breaks are not natural transcript paragraph breaks.
        for txt in ("清淨心菩提種子\n", "清淨心\n\n菩提種子\n",
                    "清淨心　菩提種子\n"):
            with self.subTest(txt=txt):
                result = evaluate_auto_acceptance(
                    **(self.params | {"txt_bytes": txt.encode("utf-8")}))
                self.assertTrue(result["can_close_without_human"])
                self.assertFalse(result["formal_golden_pass"])
                self.assertNotIn("txt_does_not_match_srt_in_order",
                                 result["technical_blockers"])

    def test_natural_paragraph_policy_keeps_lexical_changes_blocked(self):
        for txt in ("清淨心\n菩提佛子\n", "清淨心菩提種子多一字\n"):
            with self.subTest(txt=txt):
                result = evaluate_auto_acceptance(
                    **(self.params | {"txt_bytes": txt.encode("utf-8")}))
                self.assertIn("txt_does_not_match_srt_in_order",
                              result["technical_blockers"])
                self.assertFalse(result["can_close_without_human"])

    def test_mismatched_srt_txt_is_technical_failure(self):
        x=evaluate_auto_acceptance(**(self.params|{'txt_bytes':b'mismatch'}))
        self.assertEqual(x['status'],'AUTO_BLOCKED_TECHNICAL')
        self.assertFalse(x['can_close_without_human'])
        self.assertIn('txt_does_not_match_srt_in_order',x['technical_blockers'])

    def test_subtitle_overshoot_fails(self):
        x=evaluate_auto_acceptance(**(self.params|{'source_duration_ms':5000}))
        self.assertEqual(x['status'],'AUTO_BLOCKED_TECHNICAL')
        self.assertIn('subtitles_extend_past_source_audio',x['technical_blockers'])

    def test_missing_source_sha_verification_blocks(self):
        x=evaluate_auto_acceptance(**(self.params|{'source_audio_sha_verified':False}))
        self.assertFalse(x['can_close_without_human'])
        self.assertIn('unverified_source_audio_sha',x['technical_blockers'])

    def test_bad_srt_and_overlap_block(self):
        srt=('1\n00:00:01,000 --> 00:00:03,000\n清淨心\n\n'
             '2\n00:00:02,000 --> 00:00:04,000\n菩提\n').encode()
        x=evaluate_auto_acceptance(**(self.params|{'srt_bytes':srt}))
        self.assertFalse(x['can_close_without_human'])
        self.assertTrue(x['technical_blockers'])

    def test_reference_disagreement_is_advisory_not_golden(self):
        orig=('1\n00:00:01,000 --> 00:00:03,000\n清淨心\n\n'
              '2\n00:00:07,000 --> 00:00:09,000\n菩提種子啊\n').encode()
        x=evaluate_auto_acceptance(**(self.params|{'original_srt_bytes':orig}))
        self.assertEqual(x['status'],TIER)
        self.assertEqual(len(x['original_srt_disagreements']),1)
        self.assertFalse(x['formal_golden_pass'])

    def test_broken_optional_reference_srt_is_advisory(self):
        reference=("1\n00:00:01,000 --> 00:00:03,000\n清淨心\n\n"
                   "2\n00:00:07,000 --> 00:00:09,000\n\n").encode()
        x=evaluate_auto_acceptance(**(self.params|{'original_srt_bytes':reference}))
        self.assertEqual(x['status'],TIER)
        self.assertTrue(x['can_close_without_human'])
        self.assertEqual(x['original_srt_disagreements'][0]['type'],'INVALID_REFERENCE_SRT')
        self.assertFalse(x['formal_golden_pass'])

    def test_no_references_allowed_with_report(self):
        x=evaluate_auto_acceptance(**(self.params|{'reference_txt_bytes':None,
                           'original_srt_bytes':None,'acoustic_review_items':None}))
        self.assertTrue(x['can_close_without_human'])
        self.assertIsNone(x['reference_txt_comparison'])
        self.assertEqual(x['original_srt_disagreements'],[])


if __name__=='__main__':
    unittest.main()
