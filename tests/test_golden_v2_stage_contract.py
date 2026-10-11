"""Provider-free V2 sequence and cost-boundary acceptance tests."""
import unittest

from app.jobs.golden_v2_stage_contract import (
    QUALITY_3WAY, SMART_2PLUS, GEMINI_CHIRP, STAGES,
    V2Selection, authorize_v2_cost, assert_stage_can_advance, next_stage,
)

SHA = "c" * 64


class GoldenV2StageContractTests(unittest.TestCase):
    def test_primary_must_precede_chirp_and_webcodex_must_precede_final(self):
        self.assertLess(STAGES.index("gemini_primary"), STAGES.index("chirp_independent"))
        self.assertLess(STAGES.index("chirp_independent"), STAGES.index("webcodex_adjudication"))
        self.assertLess(STAGES.index("webcodex_adjudication"), STAGES.index("golden_qa"))

    def test_three_modes_keep_immutable_engine_ids(self):
        self.assertEqual(V2Selection(SHA, mode=QUALITY_3WAY).transcribe_policy, "ALL")
        self.assertEqual(V2Selection(SHA, mode=SMART_2PLUS).transcribe_policy, "RISK_WINDOWS")
        self.assertEqual(V2Selection(SHA, mode=GEMINI_CHIRP).transcribe_policy, "SKIP")
        with self.assertRaises(ValueError):
            V2Selection(SHA, model_primary="gemini-3.7-flash")

    def test_attempt_to_skip_primary_gate_is_denied(self):
        with self.assertRaises(ValueError):
            assert_stage_can_advance(
                completed_stage="preflight", next_requested="chirp_independent", evidence={}
            )
        with self.assertRaises(ValueError):
            assert_stage_can_advance(
                completed_stage="gemini_primary_completeness",
                next_requested="chirp_independent", evidence={}
            )
        assert_stage_can_advance(
            completed_stage="gemini_primary_completeness",
            next_requested="chirp_independent",
            evidence={"primary_candidate_complete": True},
        )

    def test_acoustic_and_golden_gates_cannot_be_bypassed(self):
        for stage, follow in (
            ("chirp_acoustic_gate", "transcribe_optional"),
            ("golden_qa", "export"),
        ):
            with self.assertRaises(ValueError):
                assert_stage_can_advance(
                    completed_stage=stage, next_requested=follow, evidence={}
                )

    def test_transcribe_stage_must_be_recorded_when_skipped(self):
        self.assertEqual(next_stage("chirp_acoustic_gate", transcribe_policy="SKIP"),
                         "transcribe_optional")

    def test_cost_is_bounded_by_v2_cap_and_source(self):
        authorize_v2_cost(
            approval_reference="approved-job-1", source_sha256=SHA,
            approved_source_sha256=SHA, reserved_usd="10",
            estimated_increment_usd="2.5", spent_usd="7.5",
        )
        for overrides in (
            {"approval_reference": None},
            {"approved_source_sha256": "d" * 64},
            {"estimated_increment_usd": "3"},
            {"estimated_increment_usd": "-1"},
            {"estimated_increment_usd": "NaN"},
        ):
            request = {
                "approval_reference": "approved-job-1",
                "source_sha256": SHA, "approved_source_sha256": SHA,
                "reserved_usd": "10", "estimated_increment_usd": "2.5",
                "spent_usd": "7.5",
            }
            request.update(overrides)
            with self.assertRaises(PermissionError):
                authorize_v2_cost(**request)


if __name__ == "__main__":
    unittest.main()
