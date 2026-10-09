from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.jobs import delivery_worker as worker


class DeliveryMissingArtifactCircuitBreakerTests(unittest.TestCase):
    def test_missing_local_golden_artifact_is_blocked_without_next_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = worker._schedule_failure(directory, 'DrivePublishError: Missing verified local artifact for golden_txt')
            self.assertEqual(result['status'], 'blocked_missing_artifact')
            self.assertIsNone(result['next_attempt_at'])
            self.assertFalse(worker._due(directory))
            self.assertEqual(json.loads((directory/'drive-delivery-state.json').read_text())['attempts'], 1)

    def test_legacy_pending_retry_with_same_permanent_error_is_not_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'drive-delivery-state.json').write_text(json.dumps({'status':'pending_retry','attempts':80,'last_error':'DrivePublishError: Missing verified local artifact for golden_txt'}))
            self.assertFalse(worker._due(directory))

    def test_transient_delivery_failure_remains_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = worker._schedule_failure(directory, 'DrivePublishError: timeout')
            self.assertEqual(result['status'], 'pending_retry')
            self.assertIsNotNone(result['next_attempt_at'])

    def test_editor_owned_state_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'drive-delivery-state.json').write_text(json.dumps({'status':'superseded_by_editor'}))
            self.assertTrue(worker._superseded(directory))


if __name__ == '__main__':
    unittest.main()
