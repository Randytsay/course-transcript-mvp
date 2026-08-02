from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProductionHardeningExtraTests(unittest.TestCase):
    def test_retryable_chunk_propagates_exit_76_for_backoff(self) -> None:
        from app.providers import run_chirp_pipeline_hardened as hardened

        counts = {
            "done": 2,
            "submitted": 0,
            "pending": 0,
            "retryable": 1,
            "failed": 0,
        }
        with tempfile.TemporaryDirectory() as temp, patch.object(
            hardened.base,
            "JOB",
            Path(temp),
        ), patch.object(
            hardened.base,
            "JOB_NAME",
            "job-test",
        ), patch.object(
            hardened.base,
            "_parallel_phase",
            return_value=counts,
        ), patch.object(
            hardened.base,
            "_merge",
            side_effect=AssertionError("merge must not run while retryable"),
        ):
            self.assertEqual(hardened._recover_pass([(0, 0.0, 10.0)]), 76)

    def test_global_drive_lock_persists_release_timestamp(self) -> None:
        from app.jobs.drive_lock import drive_publish_lock

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"DRIVE_GLOBAL_MIN_INTERVAL_SECONDS": "0"},
        ):
            data_dir = Path(temp)
            with drive_publish_lock(data_dir, "gdrive:course/lesson.mp3"):
                pass
            path = data_dir / "locks" / "drive-publish-global.lock"
            self.assertTrue(path.is_file())
            self.assertGreater(float(path.read_text(encoding="utf-8")), 0)


if __name__ == "__main__":
    unittest.main()
