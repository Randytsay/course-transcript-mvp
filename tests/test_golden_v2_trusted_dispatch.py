"""General dispatch guards use fake Drive/provider clients and actual SQLite."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest import mock

from tests import test_golden_v2_drive_revision as fixture
from app.pipeline import golden_v2_trusted_dispatch as dispatch
from app.jobs.golden_v2_ledger import V2Conflict


class TrustedDispatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.DriveRevisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger
        self.state = self.ledger.status(self.fixture.job)
        self.events = []

    def options(self):
        return {"storage_client": object(), "vertex_client": object(),
                "bucket_name": "private-test", "object_prefix": "v2-only",
                "cost_estimator": lambda _: 30, "cost_accountant": lambda _: 25}

    def dispatch(self, **overrides):
        def owner(_):
            self.events.append("owner")
            return True
        def capability(_):
            self.events.append("capability")
            return True
        def drive(path):
            self.events.append("drive")
            return self.fixture.live(path)
        args = dict(database=self.fixture.db, artifact_root=self.fixture.root,
                    job_id=self.fixture.job, expected_revision=self.state["revision"],
                    expected_source_sha=self.state["source_sha"],
                    expected_stage="gemini_primary", aggregate_cap_cents=1000,
                    verify_owner=owner, verify_capability_and_pricing=capability,
                    read_metadata=drive, stage_options=self.options())
        args.update(overrides)
        return dispatch.execute_trusted_paid_stage(**args)

    def complete(self, **kwargs):
        self.events.append("adapter")
        claim = kwargs["claim"]
        self.assertEqual(claim["source_sha"], self.state["source_sha"])
        self.assertEqual(kwargs["original_media"].name, "source.original")
        kwargs["ledger"].complete(claim, evidence_sha="a" * 64, actual_cents=25)
        return {"evidence_sha256": "a" * 64}

    def test_fresh_checks_then_atomic_claim_then_real_stage_entrypoint(self):
        with mock.patch.object(dispatch, "execute_approved_primary_stage",
                               autospec=True, side_effect=self.complete) as provider:
            result = self.dispatch()
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.events, ["owner", "capability", "drive", "adapter"])
        self.assertEqual(result["stage"], "gemini_primary")
        self.assertNotIn("claim", result)
        self.assertEqual(self.ledger.status(self.fixture.job)["stages"][0]["state"], "COMPLETE")

    def test_owner_or_capability_rejection_never_claims(self):
        for override in ({"verify_owner": lambda _: False},
                         {"verify_capability_and_pricing": lambda _: False}):
            with self.assertRaises(V2Conflict):
                self.dispatch(**override)
            self.assertEqual(self.ledger.status(self.fixture.job), self.state)

    def test_live_drive_change_never_claims(self):
        with self.assertRaisesRegex(V2Conflict, "Drive"):
            self.dispatch(read_metadata=lambda _: {**self.fixture.live(None), "version": "18"})
        self.assertEqual(self.ledger.status(self.fixture.job), self.state)

    def test_source_modified_by_verifier_fails_before_claim(self):
        def change(_):
            (self.fixture.root / "jobs" / self.fixture.job / "source.original").write_bytes(b"changed")
            return True
        with self.assertRaises(V2Conflict):
            self.dispatch(verify_capability_and_pricing=change)
        self.assertEqual(self.ledger.status(self.fixture.job), self.state)

    def test_wrong_revision_source_stage_or_cap_never_claims(self):
        for override in ({"expected_revision": 0}, {"expected_source_sha": "b" * 64},
                         {"expected_stage": "chirp_independent"}, {"aggregate_cap_cents": 49}):
            with self.assertRaises(V2Conflict):
                self.dispatch(**override)
        self.assertEqual(self.ledger.status(self.fixture.job), self.state)

    def test_service_cannot_replace_verified_source(self):
        with self.assertRaisesRegex(V2Conflict, "replace"):
            self.dispatch(stage_options={**self.options(), "normalized_audio": "other.flac"})
        self.assertEqual(self.ledger.status(self.fixture.job), self.state)

    def test_missing_services_leave_job_unclaimed(self):
        with self.assertRaisesRegex(V2Conflict, "configuration"):
            self.dispatch(stage_options={})
        self.assertEqual(self.ledger.status(self.fixture.job), self.state)

    def test_failure_after_claim_cannot_repeat_provider_call(self):
        with mock.patch.object(dispatch, "execute_approved_primary_stage", autospec=True,
                               side_effect=TimeoutError("ambiguous upstream result")) as provider:
            with self.assertRaises(TimeoutError):
                self.dispatch()
            with self.assertRaises(V2Conflict):
                self.dispatch()
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.ledger.status(self.fixture.job)["stages"][0]["state"], "RUNNING")

    def test_two_controllers_have_only_one_paid_stage_winner(self):
        def attempt(_):
            try:
                self.dispatch()
                return "COMPLETE"
            except V2Conflict:
                return "BLOCKED"
        with mock.patch.object(dispatch, "execute_approved_primary_stage", autospec=True,
                               side_effect=self.complete) as provider:
            with ThreadPoolExecutor(max_workers=2) as pool:
                states = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(states), ["BLOCKED", "COMPLETE"])
        self.assertEqual(provider.call_count, 1)


if __name__ == "__main__":
    unittest.main()
