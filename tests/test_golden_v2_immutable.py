"""Immutable evidence crash/collision checks; entirely offline."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_chirp_stage import _immutable as chirp_store
from app.pipeline.golden_v2_primary_stage import _persist_immutable
from app.providers.golden_v2_immutable import store_immutable_bytes


class ImmutableEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_exact_replay_preserves_raw_provider_bytes(self):
        path = self.root / "raw.json"
        original = b'{ "results" : [] }\n'
        expected = hashlib.sha256(original).hexdigest()
        self.assertEqual(chirp_store(path, original), expected)
        self.assertEqual(chirp_store(path, original), expected)
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(V2Conflict):
            chirp_store(path, b'{"results":[]}')
        self.assertEqual(path.read_bytes(), original)

    def test_preexisting_partial_or_tampered_result_blocks_replay(self):
        path = self.root / "primary-stage.json"
        path.write_bytes(b'{"truncated":')
        with self.assertRaises(V2Conflict):
            _persist_immutable(path, {"completed": True})
        self.assertEqual(path.read_bytes(), b'{"truncated":')

    def test_symlinked_immutable_destination_is_rejected_even_when_bytes_match(self):
        target = self.root / "outside.json"
        target.write_bytes(b"same bytes")
        path = self.root / "evidence.json"
        path.symlink_to(target)
        with self.assertRaisesRegex(V2Conflict, "symlink"):
            store_immutable_bytes(path, b"same bytes")
        self.assertEqual(target.read_bytes(), b"same bytes")
        self.assertTrue(path.is_symlink())

    def test_atomic_link_failure_leaves_no_final_or_temporary_artifact(self):
        path = self.root / "nested" / "new-raw.json"
        with patch("app.providers.golden_v2_immutable.os.link",
                   side_effect=OSError("simulated link failure")):
            with self.assertRaisesRegex(OSError, "simulated link failure"):
                store_immutable_bytes(path, b"durable")
        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.iterdir()), [])
        self.assertEqual(store_immutable_bytes(path, b"durable"),
                         hashlib.sha256(b"durable").hexdigest())

    def test_fsync_failure_during_private_write_leaves_no_final(self):
        path = self.root / "raw.json"
        with patch("app.providers.golden_v2_immutable.os.fsync",
                   side_effect=OSError("simulated disk failure")):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                store_immutable_bytes(path, b"complete")
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(store_immutable_bytes(path, b"complete"),
                         hashlib.sha256(b"complete").hexdigest())

    def test_two_different_primary_results_never_overwrite(self):
        path = self.root / "primary-stage.json"
        first = {"job_id": "demo", "result": "甲"}
        original_sha = _persist_immutable(path, first)
        self.assertEqual(_persist_immutable(path, first), original_sha)
        with self.assertRaises(V2Conflict):
            _persist_immutable(path, {"job_id": "demo", "result": "乙"})
        self.assertEqual(_persist_immutable(path, first), original_sha)


if __name__ == "__main__":
    unittest.main()
