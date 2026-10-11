"""Provider-free end-to-end FLAC windows using ffmpeg installed in CI."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from app.providers.golden_v2_audio_staging import (
    AudioStagingError, duration_seconds, file_sha256, prepare_local_clips,
)


def make_flac(source: Path, seconds: int = 4) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
         "-t", str(seconds), "-ac", "1", "-c:a", "flac", "-y", str(source)],
        check=True, capture_output=True, timeout=20,
    )


class LocalV2AudioStagingTests(unittest.TestCase):
    def test_real_ffmpeg_splits_and_reuses_immutable_clip(self):
        with TemporaryDirectory() as root:
            folder = Path(root)
            source = folder / "normalized.flac"
            make_flac(source)
            source_sha = file_sha256(source)
            args = dict(source=source, expected_source_sha256=source_sha,
                        output_dir=folder / "clips", max_window_seconds=2.5,
                        overlap_seconds=0.5)
            first = prepare_local_clips(**args)
            self.assertEqual(len(first["windows"]), 2)
            self.assertFalse(first["gcs_uploaded"])
            self.assertFalse(first["paid_model_called"])
            self.assertTrue(first["window_overlap_requires_reconciliation"])
            self.assertAlmostEqual(first["windows"][0]["end_seconds"], 2.5, places=2)
            self.assertEqual(first["windows"][1]["start_seconds"], 2.0)
            for window in first["windows"]:
                clip = folder / "clips" / window["clip_filename"]
                self.assertEqual(file_sha256(clip), window["clip_sha256"])
                self.assertAlmostEqual(duration_seconds(clip),
                                       window["end_seconds"]-window["start_seconds"],
                                       delta=0.05)
            # Re-reading uses existing clips; no media rewrite.
            self.assertEqual(prepare_local_clips(**args), first)
            self.assertEqual(file_sha256(source), source_sha)

    def test_source_mismatch_prevents_creation(self):
        with TemporaryDirectory() as root:
            folder = Path(root)
            source = folder / "normalized.flac"
            make_flac(source, 2)
            with self.assertRaises(AudioStagingError):
                prepare_local_clips(source=source, expected_source_sha256="0"*64,
                                    output_dir=folder / "clips", max_window_seconds=1)
            self.assertFalse((folder / "clips").exists())

    def test_tampered_existing_clip_fails_closed(self):
        with TemporaryDirectory() as root:
            folder = Path(root)
            source = folder / "normalized.flac"
            make_flac(source, 2)
            args = dict(source=source, expected_source_sha256=file_sha256(source),
                        output_dir=folder / "clips", max_window_seconds=10, overlap_seconds=0)
            report = prepare_local_clips(**args)
            clip = folder / "clips" / report["windows"][0]["clip_filename"]
            clip.write_bytes(b"poison")
            with self.assertRaises(AudioStagingError):
                prepare_local_clips(**args)

    def test_source_file_remains_readonly_and_later_manifest_fails_if_changed(self):
        with TemporaryDirectory() as root:
            folder = Path(root)
            source = folder / "normalized.flac"
            make_flac(source, 2)
            digest = file_sha256(source)
            args = dict(source=source, expected_source_sha256=digest,
                        output_dir=folder / "clips", max_window_seconds=10, overlap_seconds=0)
            prepare_local_clips(**args)
            manifest = next((folder / "clips").glob("windows.*.json"))
            record = json.loads(manifest.read_text())
            record["gcs_uploaded"] = True
            manifest.write_text(json.dumps(record))
            with self.assertRaises(AudioStagingError):
                prepare_local_clips(**args)
