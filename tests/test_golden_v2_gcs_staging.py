"""Offline GCS upload bridge uses mocks; never talks to Google Cloud."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
import subprocess

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.providers.golden_v2_audio_staging import (
    prepare_local_clips, file_sha256, AudioStagingError,
)
from app.providers.golden_v2_gcs_staging import upload_approved_clips


class Blob:
    def __init__(self):
        self.metadata = {}
        self.size = 0
        self.present = False
        self.upload_count = 0
        self.last_precondition = None

    def exists(self, *, client=None):
        return self.present

    def upload_from_filename(self, filename, *, content_type, if_generation_match):
        if self.present:
            raise AssertionError("never overwrite remote clip")
        self.last_precondition = if_generation_match
        self.upload_count += 1
        self.size = Path(filename).stat().st_size
        self.present = True

    def reload(self, *, client=None):
        return None


class FakeStorage:
    def __init__(self):
        self.blobs = {}
        self.bucket_calls = 0

    def bucket(self, name):
        self.bucket_calls += 1
        if name != "approved-private-bucket":
            raise AssertionError("wrong private bucket")
        return self

    def blob(self, name):
        return self.blobs.setdefault(name, Blob())


class GCSV2StageTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "normalized.flac"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=16000",
             "-t", "3", "-c:a", "flac", "-y", str(self.source)],
            check=True, capture_output=True, timeout=15,
        )
        self.manifest = prepare_local_clips(
            source=self.source, expected_source_sha256=file_sha256(self.source),
            output_dir=self.root / "clips", max_window_seconds=2,
            overlap_seconds=0.5,
        )
        self.db = GoldenV2Ledger(self.root / "jobs.db")
        self.job = self.db.create(
            source_sha=file_sha256(self.source), mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 10, "chirp_independent": 10,
                "compare": 0, "third_optional": 0, "adjudication": 0,
                "golden_qa": 0,
            },
        )
        self.cloud = FakeStorage()

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, **kwargs):
        args = dict(
            ledger=self.db, claim=self.claim, local_manifest=self.manifest,
            local_dir=self.root / "clips", bucket_name="approved-private-bucket",
            object_prefix="golden-v2/staged", storage_client=self.cloud,
        )
        args.update(kwargs)
        return upload_approved_clips(**args)

    def test_no_approval_has_zero_cloud_calls(self):
        self.claim = {"stage": "gemini_primary", "job_id": self.job,
                      "source_sha": file_sha256(self.source), "claim": "made-up"}
        with self.assertRaises(Exception):
            self.upload()
        self.assertEqual(self.cloud.bucket_calls, 0)

    def test_approved_upload_and_restart_reuse_are_content_addressed(self):
        self.db.approve(self.job, revision=0, approved_cents=20,
                        reference="mock", actor="owner")
        self.claim = self.db.claim_next(self.job, worker_id="mock")
        staged = self.upload()
        self.assertEqual(len(staged), 2)
        self.assertTrue(all(w["gcs_uri"].startswith("gs://approved-private-bucket/") for w in staged))
        self.assertEqual(sum(x.upload_count for x in self.cloud.blobs.values()), 2)
        self.assertEqual({x.last_precondition for x in self.cloud.blobs.values()}, {0})
        restarted = GoldenV2Ledger(self.root / "jobs.db")
        self.db = restarted
        self.assertEqual(staged, self.upload())
        self.assertEqual(sum(x.upload_count for x in self.cloud.blobs.values()), 2)
        self.assertTrue(self.db.provider_approval_validator(self.claim)(
            self.claim["claim"], self.claim["source_sha"], "gemini-3.8-flash", staged[0],
        ))

    def test_clips_changed_after_preflight_block_before_network(self):
        self.db.approve(self.job, revision=0, approved_cents=20,
                        reference="mock", actor="owner")
        self.claim = self.db.claim_next(self.job, worker_id="mock")
        clip = self.root / "clips" / self.manifest["windows"][0]["clip_filename"]
        clip.write_bytes(b"not flac")
        with self.assertRaises(AudioStagingError):
            self.upload()
        self.assertEqual(self.cloud.bucket_calls, 0)

    def test_preexisting_remote_object_requires_sha_metadata_match(self):
        self.db.approve(self.job, revision=0, approved_cents=20,
                        reference="mock", actor="owner")
        self.claim = self.db.claim_next(self.job, worker_id="mock")
        staged = self.upload()
        blob = next(iter(self.cloud.blobs.values()))
        blob.metadata["golden_v2_clip_sha256"] = "tampered"
        with self.assertRaises(AudioStagingError):
            self.upload()
        self.assertEqual(sum(x.upload_count for x in self.cloud.blobs.values()), 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
