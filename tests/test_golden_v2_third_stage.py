"""Offline ffmpeg → fake GCS → fake Vertex 3.5 end-to-end regressions."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_third_stage import execute_approved_third_stage
from app.providers.gemini_primary_audio import PrimaryTranscriptError
from tests.test_golden_v2_primary_stage import GeminiStageIntegrationTests


class ThirdStageIntegrationTests(TestCase):
    def setUp(self):
        # Reuse the audio/source lineage fixture; keep all calls local.
        self.fixture = GeminiStageIntegrationTests("test_single_file_produces_durable_gemini_primary_before_chirp")
        self.fixture.setUp()
        self.ledger = self.fixture.ledger
        self.job = self.fixture.job
        self.ledger.complete(self.fixture.claim, evidence_sha="a" * 64, actual_cents=1)
        chirp = self.ledger.claim_next(self.job, worker_id="mock-chirp")
        self.ledger.complete(chirp, evidence_sha="b" * 64, actual_cents=2)
        compare = self.ledger.claim_next(self.job, worker_id="mock-compare")
        self.ledger.complete(compare, evidence_sha="c" * 64, actual_cents=0,
                             comparison_has_dispute=True)
        self.claim = self.ledger.claim_next(self.job, worker_id="mock-third")
        self.vertex = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(
                text="復次文殊師利男身具七寶", parts=[],
                candidates=[SimpleNamespace(finish_reason="STOP")],
                usage_metadata={"prompt_token_count": 4},
            ),
        )))

    def tearDown(self):
        self.fixture.tearDown()

    def execute(self, **kw):
        args = dict(
            ledger=self.ledger, claim=self.claim,
            normalized_audio=self.fixture.source,
            original_media=self.fixture.original,
            drive_observation=self.fixture.observation,
            job_dir=self.fixture.dir / "job",
            storage_client=self.fixture.cloud, vertex_client=self.vertex,
            bucket_name="private-test-bucket", object_prefix="v2/third",
            cost_estimator=lambda m: m["local_window_count"],
            cost_accountant=lambda records: len(records),
            part_factory=lambda uri, mime: (uri, mime),
            config_factory=lambda cfg: cfg,
        )
        args.update(kw)
        return execute_approved_third_stage(**args)

    def test_complete_binds_lineage_and_real_usage(self):
        report = self.execute()
        self.assertEqual(report["timing_authority"], "CHIRP_3_ONLY")
        self.assertTrue(report["ready_for_adjudication"])
        self.assertEqual(self.ledger.status(self.job)["spent_cents"], 4)
        self.assertEqual(self.ledger.claim_next(self.job, worker_id="next")["stage"],
                         "adjudication")
        self.vertex.models.generate_content.assert_called_once()

    def test_estimate_overrun_blocks_external_calls(self):
        with self.assertRaises(V2Conflict):
            self.execute(cost_estimator=lambda m: 300)
        self.vertex.models.generate_content.assert_not_called()
        self.assertEqual(len(self.fixture.cloud.objects), 0)

    def test_known_truncation_holds_and_charges(self):
        self.vertex.models.generate_content.return_value = SimpleNamespace(
            text="未完成", parts=[], candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
            usage_metadata={"prompt_token_count": 5},
        )
        report = self.execute()
        self.assertTrue(report["needs_review"])
        state = self.ledger.status(self.job)
        self.assertEqual(state["status"], "NEEDS_REVIEW")
        self.assertEqual(state["spent_cents"], 4)
        self.assertEqual(self.vertex.models.generate_content.call_count, 1)

    def test_first_window_actual_overrun_freezes_before_another_provider_call(self):
        with self.assertRaises(V2Conflict):
            self.execute(
                max_window_seconds=2.5, overlap_seconds=0,
                cost_accountant=lambda completed: 8 * len(completed),
            )
        self.assertEqual(self.vertex.models.generate_content.call_count, 1)
        state = self.ledger.status(self.job)
        third = next(s for s in state["stages"] if s["name"] == "third_optional")
        self.assertEqual(third["state"], "OUTCOME_UNKNOWN")
        self.assertEqual(third["actual_cents"], 8)
        self.assertEqual(state["status"], "NEEDS_RECONCILIATION")
        self.assertTrue((
            self.fixture.dir / "job/golden-v2/third_optional-window-cost-hold.json"
        ).is_file())

    def test_later_disconnection_retains_prior_success_cost(self):
        success = self.vertex.models.generate_content.return_value
        self.vertex.models.generate_content.side_effect = [success, TimeoutError("disconnect")]
        with self.assertRaises(PrimaryTranscriptError):
            self.execute(max_window_seconds=2.5, overlap_seconds=0)
        state = self.ledger.status(self.job)
        third = next(s for s in state["stages"] if s["name"] == "third_optional")
        self.assertEqual(third["state"], "OUTCOME_UNKNOWN")
        self.assertEqual(third["actual_cents"], 1)
        self.assertEqual(state["spent_cents"], 4)
        self.assertTrue((self.fixture.dir/"job/golden-v2/third-ambiguous.json").exists())
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job, worker_id="no-replay")
        self.assertEqual(self.vertex.models.generate_content.call_count, 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
