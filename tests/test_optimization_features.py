import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.jobs.artifacts import cleanup_completed_audio
from app.operations.retention_cleanup import build_report
from app.providers.qa_report import density_windows
from app.providers.subtitle_cleanup import build_report as cleanup_report


class OptimizationFeatureTests(unittest.TestCase):
    def test_completed_audio_cleanup_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            (job / "chunks" / "chunk-001").mkdir(parents=True)
            (job / "chunks" / "chunk-001" / "attempts" / "retry-001").mkdir(parents=True)
            (job / "normalized.flac").write_bytes(b"audio")
            (job / "chunks" / "chunk-001" / "audio.flac").write_bytes(b"chunk")
            (job / "chunks" / "chunk-001" / "attempts" / "retry-001" / "audio.flac").write_bytes(b"retry")
            (job / "merged-words.json").write_text("{}", encoding="utf-8")
            report = cleanup_completed_audio(job)
            self.assertEqual(report["status"], "PASS")
            self.assertFalse((job / "normalized.flac").exists())
            self.assertFalse((job / "chunks" / "chunk-001" / "audio.flac").exists())
            self.assertFalse((job / "chunks" / "chunk-001" / "attempts" / "retry-001" / "audio.flac").exists())
            self.assertTrue((job / "merged-words.json").exists())

    def test_density_flags_outlier_and_creates_patch_shape(self):
        windows, plans = density_windows(
            [{"start_ms": 0, "end_ms": 1000, "raw_text": "字" * 10}],
            900_000,
        )
        self.assertEqual(windows[0]["reason"], "char_count=10<2500")
        self.assertEqual(plans[0]["reason"], "density_out_of_range")

    def test_mantra_duplicate_cycle_is_canonicalized(self):
        lines = ["《得見彌勒根本大明神咒》"] + [
            "南謨囉怛那怛囉夜耶。", "南謨吠嚕左那莎彌儞。", "怛他誐多耶。", "阿囉喝帝三藐三沒馱耶。"
        ] * 2
        segments = [
            {"segment_id": f"s{i}", "start_ms": 800_000 + i * 1000, "end_ms": 801_000 + i * 1000, "raw_text": text, "corrected_text": text}
            for i, text in enumerate(lines)
        ]
        report = cleanup_report("subtitles.json", segments)
        self.assertTrue(report["mantra"]["applied"])
        self.assertEqual(report["segments"][0]["cleaned_text"], "《得見彌勒根本大明神咒》")
        self.assertTrue(any("mantra_canonicalized" in item["cleanup_actions"] for item in report["segments"]))

    def test_retention_is_dry_run_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "jobs" / "job-1"
            state_dir.mkdir(parents=True)
            (state_dir / "drive-publish-state.json").write_text(json.dumps({
                "status": "completed",
                "files": {"srt": {"backup_remote_path": "gdrive:folder/file.backup.srt", "backup_created_at": "2020-01-01T00:00:00+00:00"}},
            }), encoding="utf-8")
            report = build_report(root, now=datetime(2026, 8, 10, tzinfo=UTC), apply=False)
            self.assertFalse(report["apply"])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(len(report["drive_backups"]["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
