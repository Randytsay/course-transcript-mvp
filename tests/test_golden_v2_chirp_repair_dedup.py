"""Provider-free regression for completed Chirp repair overlap and review."""
import json
import unittest

from app.pipeline.golden_v2_chirp_stage import suppress_completed_repair_proposals


class RepairDedupTests(unittest.TestCase):
    @staticmethod
    def proposal(start, end):
        return {
            "start_ms": start, "end_ms": end,
            "target_intervals": [{"start_ms": start, "end_ms": end}],
        }

    @staticmethod
    def completed(start, end, *, status="DONE"):
        return {
            "purpose": "REPAIR", "status": status,
            "target_intervals_json": json.dumps([
                {"start_ms": start, "end_ms": end}
            ]),
        }

    def test_repeated_window_review_not_resubmitted(self):
        proposed = self.proposal(7934160, 7950960)
        report = {"status": "REVIEW", "repair_plan": {
            "status": "PROPOSED_REQUIRES_APPROVAL",
            "intervals": [proposed], "total_duration_ms": 16800}}
        out = suppress_completed_repair_proposals(
            report, [self.completed(7855700, 8013460)])
        self.assertEqual(out["status"], "REVIEW")
        self.assertEqual(out["repair_plan"]["intervals"], [])
        self.assertEqual(out["repair_plan"]["status"], "NONE")
        self.assertTrue(out["repair_plan"]["manual_review_required"])
        self.assertEqual(len(out["repair_plan"]
                             ["completed_repair_overlap_review_required"]), 1)
        self.assertEqual(report["repair_plan"]["intervals"], [proposed])

    def test_disjoint_new_gap_kept(self):
        a = self.proposal(100, 300)
        b = self.proposal(500, 700)
        report = {"status": "REVIEW", "repair_plan": {
            "intervals": [a, b], "status": "PROPOSED_REQUIRES_APPROVAL"}}
        out = suppress_completed_repair_proposals(
            report, [self.completed(0, 350)])
        self.assertEqual(out["repair_plan"]["intervals"], [b])
        self.assertEqual(out["repair_plan"]["total_duration_ms"], 200)
        self.assertTrue(out["repair_plan"]["manual_review_required"])

    def test_pending_attempt_not_treated_as_complete(self):
        a = self.proposal(100, 300)
        report = {"status": "REVIEW", "repair_plan": {"intervals": [a]}}
        self.assertIs(suppress_completed_repair_proposals(
            report, [self.completed(0, 350, status="SUBMITTED")]), report)


if __name__ == "__main__":
    unittest.main()
