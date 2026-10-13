"""Mocked private GCS upload + real-model wiring: zero network/paid calls."""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from app.pipeline.golden_v2_smoke_dispatch import execute_trusted_smoke_stage
from app.pipeline.golden_v2_smoke_gcs import stage_clip_once, verify_staged_clip
from app.pipeline.golden_v2_smoke_ledger import SmokeConflict
from app.providers.golden_v2_smoke_providers import SmokeProviderError

BUCKET = "private-test"


class FakeGCSBlob:
    def __init__(self):
        self.uploads = []
        self.generation = None
        self.bytes = None
        self.metadata = None

    def upload_from_filename(self, path, **kwargs):
        self.uploads.append(kwargs)
        if self.bytes is not None:
            raise ValueError("simulated generation precondition failed")
        self.bytes = Path(path).read_bytes()
        self.generation = 7

    def download_as_bytes(self, **kwargs):
        if kwargs["if_generation_match"] != self.generation:
            raise ValueError("stale GCS generation")
        return self.bytes


class FakeGCS:
    def __init__(self):
        self.objects = {}
        self.requested = []

    def bucket(self, bucket):
        self.requested.append(bucket)
        return self

    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = FakeGCSBlob()
        return self.objects[name]


class FakeTrial:
    def __init__(self, root):
        self.excerpt_audio = root / "real-4min.flac"
        self.excerpt_audio.write_bytes(b"a" * 8241422)
        self.plan_path = root / "plan.json"
        self.plan_path.write_bytes(b"{}")
        self.sha = hashlib.sha256(self.excerpt_audio.read_bytes()).hexdigest()
        self.pending = True
        self.invocations = []
        self.events = []

    def record_provider_event(self, *, model, event):
        self.events.append((model, event))

    def _scope(self):
        return {
            "clip_sha256": self.sha, "source_drive_file_id": "drive-parent",
            "source_drive_version": "33",
            "source_original_sha256": "b" * 64,
        }

    def status(self):
        return {"provider_calls_started": not self.pending}

    def execute_one(self, *, model, provider_call, cost_accountant,
                    verify_owner, verify_capability_and_pricing):
        assert verify_owner(self._scope()) is True
        assert verify_capability_and_pricing(self._scope(), model) is True
        self.invocations.append(model)
        result = provider_call(self.excerpt_audio, model)
        return {"model": model, "accounted_cents": cost_accountant(result),
                "response": result}


class SmokeGcsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.trial = FakeTrial(self.root)
        self.storage = FakeGCS()

    def stage(self):
        return stage_clip_once(
            trial=self.trial, storage_client=self.storage,
            bucket=BUCKET, check_owner=lambda _: True,
            check_bucket=lambda _, __: True,
        )

    def test_exact_clip_upload_generation_bound_and_sha_roundtrip(self):
        report = self.stage()
        self.assertEqual(report["clip_sha256"], self.trial.sha)
        self.assertFalse(report["provider_calls_started"])
        key = f"golden-v2-smoke/{self.trial.sha}/clip.flac"
        blob = self.storage.objects[key]
        self.assertEqual(len(blob.uploads), 1)
        self.assertEqual(blob.uploads[0]["if_generation_match"], 0)
        self.assertIsNone(blob.uploads[0]["retry"])
        self.assertEqual(blob.metadata["golden-v2-clip-sha256"], self.trial.sha)
        verified = verify_staged_clip(
            trial=self.trial, storage_client=self.storage, bucket=BUCKET,
        )
        self.assertTrue(verified["verified_without_mutation"])
        self.assertEqual(verified["generation"], "7")
        with self.assertRaisesRegex(SmokeConflict, "existing paid receipt"):
            self.stage()
        self.assertEqual(len(blob.uploads), 1)

    def test_upload_fails_before_mutation_without_owner_or_bucket(self):
        with self.assertRaisesRegex(SmokeConflict, "approval missing"):
            stage_clip_once(
                trial=self.trial, storage_client=self.storage,
                bucket=BUCKET, check_owner=lambda _: False,
                check_bucket=lambda *_: True,
            )
        self.assertEqual(self.storage.objects, {})
        self.assertFalse((self.root / "gcs-upload-intent.json").exists())

    def test_upload_timeout_not_retried(self):
        self.storage.blob("golden-v2-smoke/" + self.trial.sha + "/clip.flac")
        blob = list(self.storage.objects.values())[0]
        def failed_upload(*_, **__):
            blob.uploads.append(1)
            raise TimeoutError("cloud outcome unknown")
        blob.upload_from_filename = failed_upload
        with self.assertRaises(TimeoutError):
            self.stage()
        self.assertTrue((self.root / "gcs-upload-intent.json").exists())
        with self.assertRaisesRegex(SmokeConflict, "existing paid receipt"):
            self.stage()
        self.assertEqual(len(blob.uploads), 1)

    def test_verify_does_not_trust_manifest_without_bytes(self):
        self.stage()
        blob = list(self.storage.objects.values())[0]
        blob.bytes = b"tampered remote bytes"
        with self.assertRaisesRegex(SmokeConflict, "GCS bytes differ"):
            verify_staged_clip(
                trial=self.trial, storage_client=self.storage, bucket=BUCKET,
            )

    def test_one_mock_gemini_dispatch_after_verified_source(self):
        self.stage()
        calls = []
        response = NS(
            text="佛學法會測試結果",
            parts=[],
            model_dump=lambda **_: {
                "candidates": [{"finish_reason": "STOP"}],
                "usage_metadata": {
                    "prompt_token_count": 3000,
                    "candidates_token_count": 700,
                },
            },
        )
        vertex_client = NS(models=NS(generate_content=lambda **kwargs: (
            calls.append(kwargs) or response
        )))
        result = execute_trusted_smoke_stage(
            trial=self.trial, model="gemini-3.8-flash",
            project="test-project", bucket=BUCKET,
            storage_client=self.storage, vertex_client=vertex_client,
            speech_client=None,
            verify_owner=lambda _: True,
            verify_capability_and_pricing=lambda _, __: True,
            gemini_part_factory=lambda uri: ("flac", uri),
            gemini_config_factory=lambda model: model,
        )
        self.assertEqual(result["accounted_cents"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "gemini-3.8-flash")
        self.assertEqual(self.trial.invocations, ["gemini-3.8-flash"])
        self.assertEqual(self.trial.events[0][1]["kind"], "gemini_response")


if __name__ == "__main__":
    unittest.main()
