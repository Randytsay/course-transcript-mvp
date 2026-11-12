"""Offline delivery re-entry gate: old owner grants must not bypass QA."""
import unittest

from scripts.golden_v2_delivery_daemon import publication_gate


class DeliveryDaemonGateTests(unittest.TestCase):
    def test_needs_review_and_pending_qa_defer_without_drive_call(self):
        state = {'status': 'NEEDS_REVIEW', 'stages': [
            {'name': 'golden_qa', 'state': 'PENDING'}]}
        self.assertEqual(publication_gate(state), 'GOLDEN_QA_NOT_READY')

    def test_only_final_review_with_complete_qa_can_attempt_publish(self):
        self.assertEqual(publication_gate({'status': 'AWAITING_FINAL_REVIEW',
            'stages': [{'name': 'golden_qa', 'state': 'COMPLETE'}]}), None)
        self.assertEqual(publication_gate({'status': 'PROCESSING',
            'stages': [{'name': 'golden_qa', 'state': 'COMPLETE'}]}), 'GOLDEN_QA_NOT_READY')

    def test_missing_or_malformed_qa_stage_fails_closed(self):
        self.assertEqual(publication_gate({'status': 'AWAITING_FINAL_REVIEW', 'stages': []}),
                         'GOLDEN_QA_NOT_READY')


if __name__ == '__main__':
    unittest.main()
