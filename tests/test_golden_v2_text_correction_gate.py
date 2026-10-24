"""Admissibility of text corrections over timing-authoritative Chirp text."""
import unittest

from app.pipeline.golden_v2_text_correction_gate import (
    PROBE_RECEIPT_SCHEMA, TextCorrectionGateError,
    assess_text_corrections, require_admissible_text_corrections,
)

SOURCE = 'a' * 64
WORDS = [
    {'word': '所', 'start_ms': 1_000, 'end_ms': 1_100},
    {'word': '以', 'start_ms': 1_100, 'end_ms': 1_200},
    {'word': '必', 'start_ms': 1_200, 'end_ms': 1_300},
    {'word': '須', 'start_ms': 1_300, 'end_ms': 1_400},
    {'word': '先', 'start_ms': 1_400, 'end_ms': 1_500},
    {'word': '修', 'start_ms': 1_500, 'end_ms': 1_600},
    {'word': '累', 'start_ms': 1_600, 'end_ms': 1_700},
    {'word': '積', 'start_ms': 1_700, 'end_ms': 1_800},
    {'word': '你', 'start_ms': 1_800, 'end_ms': 1_900},
    {'word': '的', 'start_ms': 1_900, 'end_ms': 2_000},
    {'word': '一', 'start_ms': 2_000, 'end_ms': 2_100},
    {'word': '切', 'start_ms': 2_100, 'end_ms': 2_200},
    {'word': '功', 'start_ms': 2_200, 'end_ms': 2_300},
    {'word': '德', 'start_ms': 2_300, 'end_ms': 2_400},
    {'word': '之', 'start_ms': 2_400, 'end_ms': 2_500},
    {'word': '後', 'start_ms': 2_500, 'end_ms': 2_600},
    {'word': '才', 'start_ms': 2_600, 'end_ms': 2_700},
    {'word': '能', 'start_ms': 2_700, 'end_ms': 2_800},
    {'word': '萬', 'start_ms': 2_800, 'end_ms': 2_900},
    {'word': '宗', 'start_ms': 2_900, 'end_ms': 3_000},
    {'word': '歸', 'start_ms': 3_000, 'end_ms': 3_100},
    {'word': '一', 'start_ms': 3_100, 'end_ms': 3_200},
]
SPAN = [19, 20]


def correction(**changes):
    record = {
        'id': 103,
        'before': '宗歸',
        'after': '中取',
        'source_word_indices': list(SPAN),
        'start_ms': 2_900,
        'end_ms': 3_100,
        'primary_raw_sha256': 'b' * 64,
        'native_primary_context': '修累積你的一切功德之後才能萬中取一那個時候',
        'reviewer_kind': 'AI_SOURCE_BOUND_TEXT_REVIEW',
        'human_listening_attested': False,
    }
    record.update(changes)
    return record


def receipt(**changes):
    record = {
        'schema': PROBE_RECEIPT_SCHEMA,
        'source_sha256': SOURCE,
        'clip_start_ms': 2_800,
        'clip_end_ms': 3_300,
        'response_text': '才能萬中取一那個時候不是末法時期',
        'model': 'gemini-3.8-flash',
        'clip_sha256': 'c' * 64,
    }
    record.update(changes)
    return record


def assess(records, receipts=None):
    return assess_text_corrections(corrections=records, words=WORDS,
                                   source_sha=SOURCE,
                                   probe_receipts=receipts or {})


class SecondEngineOnlyReadingTests(unittest.TestCase):
    def test_unlistened_second_engine_reading_may_not_overwrite_chirp_text(self):
        report = assess([correction()])
        item = report['corrections'][0]
        self.assertEqual(item['status'], 'DOWNGRADED_TO_CHIRP')
        self.assertEqual(item['evidence_class'], 'SECOND_ENGINE_TEXT_ONLY')
        self.assertIn('unlistened_second_engine_reading', item['reasons'])
        self.assertEqual(item['retained_text'], '宗歸')
        self.assertEqual(item['proposed_text'], '中取')
        self.assertEqual(report['blocking_correction_count'], 1)
        self.assertFalse(report['safe_to_publish'])

    def test_multiple_unlistened_readings_are_all_downgraded(self):
        report = assess([correction(), correction(id=91, before='歸一', after='取一',
                                                  source_word_indices=[20, 21])])
        self.assertEqual(report['correction_count'], 2)
        self.assertEqual(report['blocking_correction_count'], 2)
        self.assertEqual(report['downgraded_count'], 2)
        self.assertFalse(report['safe_to_publish'])

    def test_second_engine_context_never_counts_as_listening(self):
        report = assess([correction(human_listening_attested=True, reviewer=None,
                                    review_reference=None)])
        self.assertEqual(report['corrections'][0]['status'], 'DOWNGRADED_TO_CHIRP')


class HumanListeningTests(unittest.TestCase):
    def test_named_listener_may_overwrite_chirp_text(self):
        report = assess([correction(human_listening_attested=True,
                                    reviewer='owner-local',
                                    review_reference='session-20261023-01')])
        item = report['corrections'][0]
        self.assertEqual(item['status'], 'ADMISSIBLE')
        self.assertEqual(item['evidence_class'], 'HUMAN_LISTENING')
        self.assertEqual(report['blocking_correction_count'], 0)
        self.assertTrue(report['safe_to_publish'])

    def test_listening_flag_without_identity_or_reference_is_refused(self):
        for missing in ('reviewer', 'review_reference'):
            record = correction(human_listening_attested=True,
                                reviewer='owner-local',
                                review_reference='session-20261023-01')
            record[missing] = None
            report = assess([record])
            self.assertEqual(report['corrections'][0]['status'], 'DOWNGRADED_TO_CHIRP')
            self.assertIn('listening_attestation_incomplete', report['corrections'][0]['reasons'])

    def test_ai_may_not_attest_human_listening(self):
        report = assess([correction(human_listening_attested=True,
                                    reviewer='AI_SOURCE_BOUND_TEXT_REVIEW',
                                    review_reference='gemini-3.8-flash')])
        self.assertEqual(report['corrections'][0]['status'], 'DOWNGRADED_TO_CHIRP')
        self.assertIn('listener_is_not_human', report['corrections'][0]['reasons'])


class AudioNativeProbeTests(unittest.TestCase):
    def test_source_bound_probe_covering_the_span_is_admissible(self):
        digest = 'd' * 64
        report = assess([correction(probe_receipt_sha256=digest)],
                        {digest: receipt()})
        item = report['corrections'][0]
        self.assertEqual(item['status'], 'ADMISSIBLE')
        self.assertEqual(item['evidence_class'], 'AUDIO_NATIVE_PROBE')
        self.assertEqual(item['evidence_sha256'], digest)

    def test_probe_that_does_not_cover_the_whole_span_is_refused(self):
        digest = 'd' * 64
        report = assess([correction(probe_receipt_sha256=digest)],
                        {digest: receipt(clip_start_ms=2_950, clip_end_ms=3_300)})
        item = report['corrections'][0]
        self.assertEqual(item['status'], 'DOWNGRADED_TO_CHIRP')
        self.assertIn('probe_clip_does_not_cover_edited_span', item['reasons'])

    def test_probe_for_another_source_is_refused(self):
        digest = 'd' * 64
        report = assess([correction(probe_receipt_sha256=digest)],
                        {digest: receipt(source_sha256='e' * 64)})
        self.assertIn('probe_source_mismatch',
                      report['corrections'][0]['reasons'])

    def test_probe_that_does_not_contain_the_replacement_is_refused(self):
        digest = 'd' * 64
        report = assess([correction(probe_receipt_sha256=digest)],
                        {digest: receipt(response_text='才能萬宗歸一那個時候')})
        self.assertIn('probe_text_does_not_support_replacement',
                      report['corrections'][0]['reasons'])

    def test_unresolvable_probe_reference_is_refused(self):
        report = assess([correction(probe_receipt_sha256='f' * 64)], {})
        self.assertIn('probe_receipt_missing', report['corrections'][0]['reasons'])

    def test_malformed_probe_receipt_is_refused(self):
        digest = 'd' * 64
        for bad in ({'schema': 'other'}, {'clip_start_ms': 'x'},
                    {'response_text': None}, {'clip_end_ms': 1}):
            report = assess([correction(probe_receipt_sha256=digest)],
                            {digest: receipt(**bad)})
            self.assertEqual(report['corrections'][0]['status'], 'DOWNGRADED_TO_CHIRP')
            self.assertIn('probe_receipt_malformed',
                          report['corrections'][0]['reasons'])


class PunctuationAttachmentTests(unittest.TestCase):
    """Chirp may attach a sentence-final mark to the last word of a span.

    A claimed `before` that omits that mark still describes the same words, so
    the binding is not a mismatch; a lexical difference must still be refused.
    """

    WORDS = [
        {'word': '同', 'start_ms': 10, 'end_ms': 20},
        {'word': '佛', 'start_ms': 20, 'end_ms': 30},
        {'word': '接', 'start_ms': 30, 'end_ms': 40},
        {'word': '引。', 'start_ms': 40, 'end_ms': 50},
    ]

    def _assess(self, indices):
        return assess_text_corrections(
            corrections=[{'id': 1, 'before': '同佛接引', 'after': '蒙佛接引',
                          'source_word_indices': indices}],
            words=self.WORDS, source_sha=SOURCE, probe_receipts={})

    def test_claimed_before_without_the_attached_mark_still_matches(self):
        item = self._assess([0, 1, 2, 3])['corrections'][0]
        self.assertNotIn('before_text_does_not_match_chirp_words', item['reasons'])
        self.assertEqual(item['retained_text'], '同佛接引。')

    def test_lexical_difference_is_still_refused(self):
        item = self._assess([0, 1])['corrections'][0]
        self.assertIn('before_text_does_not_match_chirp_words', item['reasons'])


class BindingTests(unittest.TestCase):
    def test_correction_must_match_the_words_it_claims_to_edit(self):
        report = assess([correction(before='萬宗', after='萬取')])
        item = report['corrections'][0]
        self.assertEqual(item['status'], 'DOWNGRADED_TO_CHIRP')
        self.assertIn('before_text_does_not_match_chirp_words', item['reasons'])
        self.assertEqual(item['retained_text'], '宗歸')

    def test_out_of_range_word_index_is_rejected(self):
        with self.assertRaises(TextCorrectionGateError):
            assess([correction(source_word_indices=[99])])

    def test_non_monotonic_indices_are_rejected(self):
        with self.assertRaises(TextCorrectionGateError):
            assess([correction(source_word_indices=[20, 19])])

    def test_blank_replacement_is_rejected(self):
        with self.assertRaises(TextCorrectionGateError):
            assess([correction(after='  ')])

    def test_identical_before_and_after_is_rejected(self):
        with self.assertRaises(TextCorrectionGateError):
            assess([correction(after='宗歸')])

    def test_empty_correction_list_is_trivially_publishable(self):
        report = assess([])
        self.assertEqual(report['correction_count'], 0)
        self.assertTrue(report['safe_to_publish'])
        self.assertEqual(report['retained_chirp_only'], True)

    def test_report_never_claims_content_certification(self):
        report = assess([correction()])
        self.assertFalse(report['golden_pass'])
        self.assertFalse(report['human_listening_attested'])
        self.assertEqual(report['timing_authority'], 'chirp_3')
        self.assertTrue(report['second_engine_text_is_not_acoustic_evidence'])


class RequireTests(unittest.TestCase):
    def test_requirement_raises_and_lists_every_downgraded_correction(self):
        with self.assertRaises(TextCorrectionGateError) as raised:
            require_admissible_text_corrections(corrections=[correction()],
                                               words=WORDS, source_sha=SOURCE)
        message = str(raised.exception)
        self.assertIn('103', message)
        self.assertIn('宗歸', message)

    def test_requirement_returns_report_when_every_correction_is_admissible(self):
        digest = 'd' * 64
        report = require_admissible_text_corrections(
            corrections=[correction(probe_receipt_sha256=digest)],
            words=WORDS, source_sha=SOURCE, probe_receipts={digest: receipt()})
        self.assertTrue(report['safe_to_publish'])


if __name__ == '__main__':
    unittest.main()