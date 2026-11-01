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

    def test_no_references_allowed_with_report(self):
        x=evaluate_auto_acceptance(**(self.params|{'reference_txt_bytes':None,
                           'original_srt_bytes':None,'acoustic_review_items':None}))
        self.assertTrue(x['can_close_without_human'])
        self.assertIsNone(x['reference_txt_comparison'])
        self.assertEqual(x['original_srt_disagreements'],[])


if __name__=='__main__':
    unittest.main()
