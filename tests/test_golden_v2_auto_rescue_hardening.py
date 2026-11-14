"""Regression tests for source indices, claim recovery, and paid dispatch safety."""
import hashlib
import json
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_worker import run_v2_worker_pass
from app.pipeline.golden_v2_paid_executor import run_paid_executor_pass
from app.pipeline.golden_v2_stage_runner import _correction_matches_candidate
from app.pipeline.golden_v2_partial_primary import refused_span_pieces
from app.providers.golden_v2_compare import compare_gemini_chirp, AlignmentNeedsReview
from app.subtitles.golden_v2_render import _word_evidence
from scripts.golden_v2_auto_rescue_hook import (
    run_auto_rescue_hook_cycle, inspect_auto_rescue_candidates,
)
from tests.test_golden_v2_auto_rescue import (
    _AutoRescueCase, build_held_job, claim_rescue, complete_chirp,
)


class SourceIndexRegressionTests(unittest.TestCase):
    def test_comparison_retains_full_stream_evidence_after_middle_refusal(self):
        sha = "a" * 64
        words = [
            {"word": c, "start_ms": 1000 * i, "end_ms": 1000 * i + 500}
            for i, c in enumerate("甲乙丙丁")
        ]
        # In the real stream 1,2 are refused, but 3 is comparable.
        in_scope = [words[0], words[3]]
        report = compare_gemini_chirp(
            "甲戊", in_scope, source_sha256=sha, word_indices=[0, 3])
        candidates = [x for x in report["candidates"]
                      if x["chirp_evidence_ids"]]
        self.assertTrue(candidates)
        corrected = next(x for x in candidates
                         if x.get("chirp_source_word_indices") == [3])
        evidence_sha = hashlib.sha256(
            f"{sha}|3|丁|3000|3500".encode()).hexdigest()
        self.assertEqual(corrected["chirp_evidence_ids"], [evidence_sha])
        self.assertTrue(_correction_matches_candidate(
            correction={
                "start_word": 3, "end_word": 4,
                "chirp_word_evidence_shas": [_word_evidence(words[3], 3, sha)]
            },
            candidate=corrected, words=words, source_sha=sha))

    def test_refused_without_chirp_words_requires_review(self):
        candidates, spans = refused_span_pieces(
            words=[],
            refused_windows=[{
                "start_seconds": 10.0, "end_seconds": 270.0,
                "block_reason": "SAFETY",
            }],
            source_sha256="a" * 64)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(spans, [])
        self.assertTrue(all(
            item["comparison_status"] == "REVIEW_REQUIRED"
            and item["auto_replacement_allowed"] is False
            and item["chirp_authoritative"] is False
            and item["source_time_end"] - item["source_time_start"] <= 120000
            for item in candidates))
        self.assertEqual(candidates[0]["source_time_start"], 10000)
        self.assertEqual(candidates[-1]["source_time_end"], 270000)

    def test_non_monotonic_source_mapping_fails_closed(self):
        w = [{"word": "甲", "start_ms": 0, "end_ms": 100}]
        for mapping in ([1, 2], [-1], [True]):
            with self.subTest(mapping=mapping), self.assertRaises(AlignmentNeedsReview):
                compare_gemini_chirp("甲", w, source_sha256="b" * 64,
                                     word_indices=mapping)


class RescueRecoveryRegressionTests(_AutoRescueCase):
    def _rescued(self):
        fixture = self.held()
        chirp_claim = claim_rescue(fixture, self.ledger)
        complete_chirp(fixture, self.ledger, chirp_claim)
        return fixture

    def _assert_stage_recovered(self, target):
        fixture = self._rescued()
        comparison = self.ledger.claim_next(
            fixture["job_id"], worker_id="compare")
        self.ledger.complete(
            comparison, evidence_sha="a" * 64, actual_cents=0,
            comparison_has_dispute=True)
        adjudication = self.ledger.claim_next(
            fixture["job_id"], worker_id="review")
        self.assertEqual(adjudication["stage"], "adjudication")
        if target == "golden_qa":
            self.ledger.complete(
                adjudication, evidence_sha="b" * 64,
                actual_cents=0)
            claimed = self.ledger.claim_next(
                fixture["job_id"], worker_id="qa")
            self.assertEqual(claimed["stage"], "golden_qa")
        else:
            claimed = adjudication
        with self.ledger._tx() as db:
            db.execute(
                "UPDATE v2_stages SET updated_at=? "
                "WHERE job_id=? AND name=?",
                ("2020-01-01T00:00:00+00:00",
                 fixture["job_id"], target))
        revision = self.ledger.status(fixture["job_id"])["revision"]
        recovered = self.ledger.reclaim_stale_local(
            fixture["job_id"], stage=target, worker_id="recovery",
            minimum_age_seconds=300, expected_revision=revision)
        self.assertEqual(recovered["stage"], target)
        self.ledger.assert_stage_claim(recovered, stage=target)
        with self.assertRaises(V2Conflict):
            self.ledger.assert_stage_claim(claimed, stage=target)

    def test_stale_adjudication_recover(self):
        self._assert_stage_recovered("adjudication")

    def test_stale_golden_qa_recover(self):
        self._assert_stage_recovered("golden_qa")

    def test_active_work_not_starved_by_old_held_jobs(self):
        old = [self.held() for _ in range(3)]
        active = self.ledger.create(
            source_sha="e" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents={
                "gemini_primary": 100, "chirp_independent": 43,
                "compare": 0, "third_optional": 0,
                "adjudication": 0, "golden_qa": 0})
        self.ledger.approve(
            active, revision=0, approved_cents=143,
            reference="queue-test", actor="owner-test")
        result = run_v2_worker_pass(
            database=self.database, artifact_root=self.root, max_jobs=2)
        self.assertEqual(result["jobs_seen"], 2)
        self.assertIn(active, [x["job_id"] for x in result["outcomes"]])
        self.assertEqual(len([
            x for x in result["outcomes"]
            if x["job_id"] in {o["job_id"] for o in old}]), 1)

    def test_paid_executor_only_recovers_existing_chirp_claim(self):
        fixture = self.held()
        claim_rescue(fixture, self.ledger)
        with patch(
            "app.pipeline.golden_v2_paid_executor.run_chirp_recovery_pass",
            return_value={"action": "WAITING_FOR_EXISTING_OPERATIONS",
                          "provider_submissions": 0},
        ) as recover:
            result = run_paid_executor_pass(
                database=self.database, artifact_root=self.root,
                enabled=True, aggregate_cap_cents=1000,
                paid_services=lambda _: {},
                recovery_services=lambda _: {
                    "gateway": object(), "cost_accountant": lambda _: 0},
                max_jobs=10)
        self.assertEqual(
            result["outcomes"][0]["action"],
            "WAITING_FOR_EXISTING_OPERATIONS")
        recover.assert_called_once()


class RescueHookCrashSafetyTests(_AutoRescueCase):
    def _script(self):
        path = Path(self.temp.name) / "unused_rescue.py"
        path.write_text("# launch is mocked; no provider call\n")
        return path

    def test_read_only_preview_does_not_touch_ledger_or_spawn(self):
        f = self.held()
        before = self.database.read_bytes()
        with patch(
            "scripts.golden_v2_auto_rescue_hook.subprocess.Popen"
        ) as spawn:
            result = inspect_auto_rescue_candidates(
                database=self.database, artifact_root=self.root)
        self.assertEqual(result["action"], "AUTO_RESCUE_READ_ONLY_PREVIEW")
        self.assertIn(f["job_id"], [x["job_id"] for x in result["outcomes"]])
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse((self.root / "auto-rescue-hook-state.json").exists())
        self.assertFalse((self.root / "auto-rescue-hook.lock").exists())
        spawn.assert_not_called()

    def test_spawn_failure_preserves_durable_intent_and_blocks_retry(self):
        f = self.held()
        kw = {"database": self.database, "artifact_root": self.root,
              "rescue_script": self._script(),
              "now": datetime(2026, 11, 14, tzinfo=timezone.utc)}
        with patch(
            "scripts.golden_v2_auto_rescue_hook.subprocess.Popen",
            side_effect=OSError("simulated fork failure"),
        ) as spawn:
            with self.assertRaises(OSError):
                run_auto_rescue_hook_cycle(**kw)
            first = json.loads(
                (self.root / "auto-rescue-hook-state.json").read_text())
            self.assertEqual(
                first["jobs"][f["job_id"]]["phase"],
                "LAUNCH_INTENT_UNCERTAIN")
            again = run_auto_rescue_hook_cycle(**kw)
            self.assertEqual(
                again["deferred"][0]["reason"],
                "PRIOR_LAUNCH_RECONCILIATION_REQUIRED")
            spawn.assert_called_once()

    def test_concurrent_cycle_does_not_spawn_twice(self):
        self.held()
        kw = {"database": self.database, "artifact_root": self.root,
              "rescue_script": self._script(),
              "now": datetime(2026, 11, 14, tzinfo=timezone.utc)}
        entered, release = threading.Event(), threading.Event()
        outputs = []

        def fake_spawn(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test launch barrier timed out")
            return SimpleNamespace(pid=12345)

        def first_cycle():
            try:
                outputs.append(run_auto_rescue_hook_cycle(**kw))
            except BaseException as exc:
                outputs.append(exc)

        with patch(
            "scripts.golden_v2_auto_rescue_hook.subprocess.Popen",
            side_effect=fake_spawn,
        ) as spawn:
            thread = threading.Thread(target=first_cycle)
            thread.start()
            self.assertTrue(entered.wait(4))
            second = run_auto_rescue_hook_cycle(**kw)
            self.assertEqual(second["action"], "AUTO_RESCUE_HOOK_BUSY")
            release.set()
            thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outputs[0]["action"],
                             "AUTO_RESCUE_HOOK_CYCLE_COMPLETE")
            spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
