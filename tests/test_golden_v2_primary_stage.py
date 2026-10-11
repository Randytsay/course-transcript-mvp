"""Offline end-to-end Gemini audio stage with real ffmpeg, fake GCS/Vertex."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_audio_staging import file_sha256
from app.pipeline.golden_v2_primary_stage import execute_approved_primary_stage


class FakeBlob:
    def __init__(self):
        self.present = False
        self.metadata = {}
        self.size = None
        self.uploads = 0

    def exists(self, *, client=None):
        return self.present

    def upload_from_filename(self, name, *, content_type, if_generation_match):
        assert if_generation_match == 0
        assert not self.present
        self.size = Path(name).stat().st_size
        self.present = True
        self.uploads += 1

    def reload(self, *, client=None):
        pass


class FakeCloud:
    def __init__(self):
        self.objects = {}

    def bucket(self, name):
        assert name == "private-test-bucket"
        return self

    def blob(self, key):
        return self.objects.setdefault(key, FakeBlob())


class GeminiStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.source = self.dir / "normalized.flac"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
             "-t", "4", "-ac", "1", "-c:a", "flac", "-y", str(self.source)],
            check=True, capture_output=True, timeout=20,
        )
        self.sha = file_sha256(self.source)
        self.ledger = GoldenV2Ledger(self.dir / "jobs.db")
        self.job = self.ledger.create(
            source_sha=self.sha, mode="QUALITY_3WAY",
            estimated_stage_cents={
                "gemini_primary": 5, "chirp_independent": 10,
                "compare": 0, "third_optional": 3,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.ledger.approve(self.job, revision=0, approved_cents=18,
                            actor="mock-owner", reference="mock-no-real-spend")
        self.claim = self.ledger.claim_next(self.job, worker_id="mock-worker")
        self.cloud = FakeCloud()
        self.vertex = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(
                text="復次文殊師利男身具七寶",
                candidates=[SimpleNamespace(finish_reason="STOP")],
                usage_metadata={"prompt_token_count": 1},
            ),
        )))

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, **options):
        args = dict(
            ledger=self.ledger, claim=self.claim,
            normalized_audio=self.source,
            job_dir=self.dir / "job",
            storage_client=self.cloud,
            vertex_client=self.vertex,
            bucket_name="private-test-bucket",
            object_prefix="v2/staging",
            cost_accountant=lambda records: len(records),
            cost_estimator=lambda manifest: manifest["local_window_count"],
            part_factory=lambda uri, mime: (uri, mime),
            max_window_seconds=9,
            overlap_seconds=0.5,
        )
        args.update(options)
        return execute_approved_primary_stage(**args)

    def test_single_file_produces_durable_gemini_primary_before_chirp(self):
        report = self.execute()
        self.assertTrue(report["ready_for_chirp"])
        self.assertEqual(report["actual_cents"], 1)
        self.assertEqual(self.vertex.models.generate_content.call_count, 1)
        self.assertEqual(self.ledger.status(self.job)["spent_cents"], 1)
        self.assertTrue((self.dir/"job/golden-v2/primary-stage.json").is_file())
        self.assertEqual(
            self.ledger.claim_next(self.job, worker_id="next")["stage"],
            "chirp_independent",
        )

    def test_overlap_is_recorded_for_review_and_billed_cost_not_hidden(self):
        report = self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        self.assertEqual(len(report["windows"]), 2)
        self.assertFalse(report["ready_for_chirp"])
        state = self.ledger.status(self.job)
        self.assertEqual(state["status"], "NEEDS_REVIEW")
        self.assertEqual(state["spent_cents"], 2)
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job, worker_id="next")

    def test_cost_preflight_excess_blocks_all_cloud_and_model_calls(self):
        with self.assertRaises(V2Conflict):
            self.execute(cost_estimator=lambda manifest: 99999)
        self.assertEqual(self.vertex.models.generate_content.call_count, 0)
        self.assertEqual(len(self.cloud.objects), 0)
        self.assertEqual(self.ledger.status(self.job)["spent_cents"], 0)

    def test_network_unknown_is_not_automatically_retried(self):
        self.vertex.models.generate_content.side_effect = TimeoutError("disconnect")
        with self.assertRaises(Exception):
            self.execute()
        state = self.ledger.status(self.job)
        self.assertEqual(state["status"], "NEEDS_RECONCILIATION")
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job, worker_id="second")
        self.assertEqual(self.vertex.models.generate_content.call_count, 1)


if __name__ == "__main__":
    unittest.main()
