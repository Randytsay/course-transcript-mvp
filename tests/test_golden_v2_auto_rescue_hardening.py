"""Regression tests for source indices, claim recovery, and paid dispatch safety."""
import hashlib
import fcntl
import os
import time
import json
import threading
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_worker import run_v2_worker_pass
from app.pipeline.golden_v2_paid_executor import run_paid_executor_pass
from app.pipeline.golden_v2_stage_runner import _correction_matches_candidate, _build_comparison_report
from app.pipeline.golden_v2_partial_primary import refused_span_pieces, build_partial_primary
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


class CrossWindowAlignmentRegressionTests(_AutoRescueCase):
    def _window_comparison(self, *, local_offset: int):
        fixture = self.held(refused_window_index=1)
        claim = claim_rescue(fixture, self.ledger)
        merged = complete_chirp(fixture, self.ledger, claim)
        partial = build_partial_primary(
            ledger=self.ledger, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"])
        stages = {x["name"]: x for x in self.ledger.status(fixture["job_id"])["stages"]}
        def fake_compare(text, words, **kwargs):
            later = words[0]["start_ms"] >= 5_380_000
            candidates = []
            if later:
                for i in range(25):
                    candidates.append({
                        "kind": "TEXT_DISAGREEMENT",
                        "chirp_char_range": [i*2, i*2+1],
                        "gemini_char_range": [i*2+local_offset, i*2+local_offset+1],
                        "source_time_start": words[i]["start_ms"],
                        "source_time_end": words[i]["end_ms"],
                        "comparison_status": "REVIEW_REQUIRED",
                    })
            return {
                "kind": "golden_v2_primary_chirp_comparison",
                "source_sha256": fixture["source_sha"],
                "candidates": candidates,
                "pending_review_count": len(candidates),
                "exact_normalized_match": not candidates,
                "similarity_ratio_not_accuracy": 1.0 if not candidates else 0.8,
            }
        with (patch("app.pipeline.golden_v2_stage_runner.compare_gemini_chirp",
                    side_effect=fake_compare),
              patch("app.pipeline.golden_v2_stage_runner.reference_comparison_hint",
                    return_value=None)):
            return _build_comparison_report(
                primary_text=partial["assembled_text"], merged=merged,
                primary=partial, claim={"job_id": fixture["job_id"],
                    "source_sha": fixture["source_sha"]},
                stages=stages, job_dir=fixture["job_dir"])

    def test_refused_prefix_does_not_create_global_offset_false_positive(self):
        report = self._window_comparison(local_offset=0)
        self.assertEqual(report["alignment_health"]["status"], "PASS")
        self.assertEqual(report["alignment_health"]["offset_scope"], "WINDOW_LOCAL")
        self.assertEqual(len(report["alignment_health"]["window_alignment_health"]), 2)
        self.assertGreaterEqual(report["pending_review_count"], 27)

    def test_true_window_local_systematic_offset_is_still_blocked(self):
        with self.assertRaisesRegex(V2Conflict, "systematic_reference_offset"):
            self._window_comparison(local_offset=600)


class AggregateBudgetRegressionTests(_AutoRescueCase):
    def test_hook_uses_independent_global_budget_and_never_consumes_attempt_on_block(self):
        first = self.held()
        second = self.held()
        marker = Path(self.temp.name) / "spawned-argv.jsonl"
        stub = Path(self.temp.name) / "fake_rescue.py"
        stub.write_text("import json,sys; open(%r,'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
                        % str(marker))
        with patch("scripts.golden_v2_auto_rescue_hook.subprocess.Popen") as spawn:
            blocked = run_auto_rescue_hook_cycle(
                database=self.database, artifact_root=self.root,
                rescue_script=stub, aggregate_cap_cents=343)
        self.assertEqual(blocked["spawned"], [])
        self.assertEqual(
            [x["reason"] for x in blocked["deferred"]],
            ["AGGREGATE_BUDGET_EXCEEDED", "AGGREGATE_BUDGET_EXCEEDED"])
        self.assertFalse((self.root / "auto-rescue-hook-state.json").exists())
        spawn.assert_not_called()
        with patch("scripts.golden_v2_auto_rescue_hook.subprocess.Popen",
                   return_value=SimpleNamespace(pid=4567)) as spawn:
            resumed = run_auto_rescue_hook_cycle(
                database=self.database, artifact_root=self.root,
                rescue_script=stub, aggregate_cap_cents=1000)
        self.assertEqual(len(resumed["spawned"]), 1)
        self.assertEqual(resumed["spawned"][0]["cap_cents"], 1000)
        self.assertEqual(resumed["spawned"][0]["approved_cents"], 343)
        self.assertIn(
            "RESCUE_ALREADY_LAUNCHED_THIS_CYCLE",
            [x["reason"] for x in resumed["deferred"]])
        self.assertEqual(
            [call.args[0][call.args[0].index("--cap-cents")+1]
             for call in spawn.call_args_list], ["1000"])

    def test_already_completed_chirp_never_becomes_eligible_again(self):
        from app.pipeline.golden_v2_auto_rescue import inspect_auto_rescue_eligibility
        f = self.held()
        claim = claim_rescue(f, self.ledger)
        complete_chirp(f, self.ledger, claim)
        with self.ledger._tx() as db:
            db.execute(
                "UPDATE v2_jobs SET status='NEEDS_REVIEW' WHERE id=?",
                (f["job_id"],))
        result = inspect_auto_rescue_eligibility(
            ledger=self.ledger, job_id=f["job_id"],
            job_dir=f["job_dir"])
        self.assertFalse(result["eligible"])
        self.assertEqual(
            result["reason"],
            "RESCUE_STAGE_ALREADY_STARTED_OR_DOWNSTREAM_ACTIVE")

    def test_job_approval_over_global_cap_is_deferred_without_launch_intent(self):
        # Total exposure 2*343c = 686c fits within the global 800c
        # ceiling; only the first job has an approval (1000c) larger than
        # the global ceiling and is guaranteed to fail the trusted claim.
        excessive = self.held(approved=1000)
        allowed = self.held(approved=343)
        stub = Path(self.temp.name) / "fake_rescue.py"
        stub.write_text("# launch is mocked; no provider call\n")
        with patch("scripts.golden_v2_auto_rescue_hook.subprocess.Popen",
                   return_value=SimpleNamespace(pid=2468)) as spawn:
            result = run_auto_rescue_hook_cycle(
                database=self.database, artifact_root=self.root,
                rescue_script=stub, aggregate_cap_cents=800)
        self.assertEqual(len(result["spawned"]), 1, result)
        self.assertEqual(result["spawned"][0]["job_id"], allowed["job_id"])
        self.assertEqual(result["spawned"][0]["cap_cents"], 800)
        self.assertIn({
            "job_id": excessive["job_id"],
            "reason": "JOB_APPROVAL_EXCEEDS_AGGREGATE_CAP",
            "approved_cents": 1000,
            "aggregate_cap_cents": 800,
        }, result["deferred"])
        self.assertEqual(spawn.call_count, 1)
        state = json.loads((self.root / "auto-rescue-hook-state.json").read_text())
        self.assertNotIn(excessive["job_id"], state["jobs"])
        self.assertEqual(state["jobs"][allowed["job_id"]]["attempts"], 1)

    def test_later_job_can_start_only_after_prior_chirp_is_complete(self):
        first = self.held()
        second = self.held()
        stub = Path(self.temp.name) / "fake_rescue.py"
        stub.write_text("# mocked, no provider\\n")
        kwargs = dict(database=self.database, artifact_root=self.root,
                      rescue_script=stub, aggregate_cap_cents=1000)
        with patch("scripts.golden_v2_auto_rescue_hook.subprocess.Popen",
                   return_value=SimpleNamespace(pid=1357)) as spawn:
            initial = run_auto_rescue_hook_cycle(**kwargs)
            self.assertEqual(len(initial["spawned"]), 1)
            self.assertEqual(len(initial["deferred"]), 1)
            pending = run_auto_rescue_hook_cycle(**kwargs)
            self.assertEqual(pending["spawned"], [])
            self.assertIn(
                "PREVIOUS_HOOK_LAUNCH_RECONCILIATION_REQUIRED",
                [x["reason"] for x in pending["deferred"]])
            fixtures = {item["job_id"]: item for item in (first, second)}
            launched_id = initial["spawned"][0]["job_id"]
            remaining_id = next(job_id for job_id in fixtures if job_id != launched_id)
            launched = fixtures[launched_id]
            rescue_claim = claim_rescue(launched, self.ledger, cap_cents=1000)
            complete_chirp(launched, self.ledger, rescue_claim)
            after = run_auto_rescue_hook_cycle(**kwargs)
            self.assertEqual(len(after["spawned"]), 1, after)
            self.assertEqual(after["spawned"][0]["job_id"], remaining_id)
            self.assertEqual(spawn.call_count, 2)

    def test_live_hook_rejects_missing_aggregate_cap_before_any_intent(self):
        f = self.held()
        stub = Path(self.temp.name) / "fake_rescue.py"
        stub.write_text("# no requests\n")
        with self.assertRaises((ValueError, V2Conflict)):
            run_auto_rescue_hook_cycle(
                database=self.database, artifact_root=self.root,
                rescue_script=stub)
        self.assertFalse((self.root / "auto-rescue-hook-state.json").exists())
        self.assertFalse((self.root / "auto-rescue-hook.lock").exists())


class RescueHookCrashSafetyTests(_AutoRescueCase):
    def _script(self):
        path = Path(self.temp.name) / "unused_rescue.py"
        path.write_text("# launch is mocked; no provider call\n")
        return path

    def test_eligibility_does_not_request_ledger_writer_transactions(self):
        from app.pipeline.golden_v2_auto_rescue import inspect_auto_rescue_eligibility
        fixture = self.held()
        # A read-only preview must NEVER call methods that request
        # BEGIN IMMEDIATE even when they happen to leave bytes unchanged.
        with patch.object(self.ledger, "status",
                          side_effect=AssertionError("writer-lock status")), \
             patch.object(self.ledger, "chirp_windows",
                          side_effect=AssertionError("writer-lock chirp")), \
             patch.object(self.ledger, "independent_rescue_resumption",
                          side_effect=AssertionError("writer-lock resumption")):
            result = inspect_auto_rescue_eligibility(
                ledger=self.ledger, job_id=fixture["job_id"],
                job_dir=fixture["job_dir"])
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["reason"], "VERIFIED_SAFETY_REFUSAL")

    def test_broken_resumption_table_fails_closed_in_read_only_preview(self):
        from app.pipeline.golden_v2_auto_rescue import inspect_auto_rescue_eligibility
        fixture = self.held()
        # Deliberately damage a test-only copy: the scanner must not
        # migrate a broken production table or claim billable eligibility.
        with self.ledger._tx() as db:
            db.execute("DROP TABLE v2_review_resumptions")
        result = inspect_auto_rescue_eligibility(
            ledger=self.ledger, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "LEDGER_STATUS_UNAVAILABLE")

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
              "now": datetime(2026, 11, 14, tzinfo=timezone.utc),
              "aggregate_cap_cents": 1000}
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

    def test_spoofed_inherited_lock_fd_fails_before_any_provider_setup(self):
        from scripts import golden_v2_independent_rescue as operator
        import sys
        fixture = self.held()
        lock_path = self.root / "paid-executor.lock"
        held_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        spoof_fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            argv = [
                "golden_v2_independent_rescue.py",
                "--root", str(self.root),
                "--job-id", fixture["job_id"],
                "--source-sha", fixture["source_sha"],
                "--revision", "2", "--cap-cents", "1000",
                "--reference", "offline-test",
                "--paid-dispatch-lock-fd", str(spoof_fd),
                "--submit",
            ]
            with patch.object(sys, "argv", argv), patch.object(
                operator, "paid_services", side_effect=AssertionError(
                    "provider initialization must not be reached")):
                with self.assertRaisesRegex(V2Conflict, "PAID_DISPATCH_BUSY"):
                    operator.main()
        finally:
            os.close(spoof_fd)
            os.close(held_fd)

    def test_normal_paid_executor_lock_defers_rescue_without_intent(self):
        self.held()
        stub = self._script()
        fd = os.open(self.root / "paid-executor.lock",
                     os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_auto_rescue_hook_cycle(
                database=self.database, artifact_root=self.root,
                rescue_script=stub, aggregate_cap_cents=1000,
                now=datetime(2026, 11, 14, tzinfo=timezone.utc))
            self.assertEqual(result["spawned"], [])
            self.assertEqual(
                [x["reason"] for x in result["deferred"]],
                ["PAID_DISPATCH_BUSY"])
            self.assertFalse(
                (self.root / "auto-rescue-hook-state.json").exists())
        finally:
            os.close(fd)

    def test_spawned_child_inherits_paid_executor_lock_until_release(self):
        self.held()
        mark = Path(self.temp.name) / "child-ready.txt"
        stub = Path(self.temp.name) / "hold-shared-dispatch.py"
        stub.write_text(
            "import os,sys,time,pathlib\n"
            "fd=int(sys.argv[sys.argv.index('--paid-dispatch-lock-fd')+1])\n"
            "os.fstat(fd)\n"
            f"pathlib.Path({str(mark)!r}).write_text('child-has-lock')\n"
            "time.sleep(1.5)\n",
            encoding="utf-8",
        )
        # Child code runs with no provider clients, network or credentials.
        result = run_auto_rescue_hook_cycle(
            database=self.database, artifact_root=self.root,
            rescue_script=stub, aggregate_cap_cents=1000)
        self.assertEqual(len(result["spawned"]), 1)
        deadline = time.monotonic() + 3.0
        while not mark.exists() and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(mark.exists(), "detached child did not inherit FD")
        fd = os.open(self.root / "paid-executor.lock", os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = run_paid_executor_pass(
                database=self.database, artifact_root=self.root,
                enabled=True, aggregate_cap_cents=1000,
                paid_services=lambda _: {}, recovery_services=lambda _: {},
                max_jobs=1)
            self.assertEqual(held["action"], "PAID_EXECUTOR_BUSY")
            # The child exits in <=1.5s; the inherited FD is then closed.
            deadline = time.monotonic() + 4.0
            acquired = False
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(.05)
            self.assertTrue(acquired, "child failed to release paid lock")
        finally:
            os.close(fd)

    def test_concurrent_cycle_does_not_spawn_twice(self):
        self.held()
        kw = {"database": self.database, "artifact_root": self.root,
              "rescue_script": self._script(),
              "now": datetime(2026, 11, 14, tzinfo=timezone.utc),
              "aggregate_cap_cents": 1000}
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


class PartialReviewEntryRegressionTests(_AutoRescueCase):
    def set_up_review(self):
        from app.pipeline.golden_v2_coordinator import run_ready_local
        fixture = self.held()
        claim = claim_rescue(fixture, self.ledger)
        complete_chirp(fixture, self.ledger, claim)
        actions = run_ready_local(
            ledger=self.ledger, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"],
        )
        self.assertEqual(
            [x["action"] for x in actions],
            ["COMPARISON_COMPLETE", "HUMAN_AUDIO_REVIEW_REQUIRED"],
        )
        state = self.ledger.status(fixture["job_id"])
        self.assertEqual(state["stages"][0]["state"], "REVIEW_REQUIRED")
        return fixture, state

    def test_partial_primary_can_export_evidence_bound_review_bundle(self):
        from app.pipeline.golden_v2_review_bundle import inspect_review_bundle
        fixture, state = self.set_up_review()
        bundle = inspect_review_bundle(
            database=self.database, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"],
            normalized_audio=fixture["job_dir"] / "normalized.flac",
        )
        self.assertEqual(bundle["revision"], state["revision"])
        self.assertEqual(bundle["source_sha256"], fixture["source_sha"])
        self.assertEqual(
            bundle["comparison_evidence_sha256"],
            next(x["evidence_sha"] for x in state["stages"]
                 if x["name"] == "compare"),
        )
        self.assertTrue(bundle["candidate_snapshot"])
        self.assertEqual(bundle["whole_file_qa"], "UNVERIFIED")

    def test_partial_primary_authenticated_review_packet_routes_to_candidates(self):
        from app import golden_v2_review_routes as api
        fixture, state = self.set_up_review()
        with patch.object(api, "_authorized_database",
                          return_value=self.database), \
             patch.object(api, "job_directory",
                          return_value=fixture["job_dir"]):
            response = api.review_packet(fixture["job_id"],
                                         authorization="test")
        self.assertEqual(response["kind"], "ADJUDICATION_AUDIO_REVIEW")
        self.assertTrue(response["human_listening_required"])
        self.assertEqual(
            response["packet"]["comparison_evidence_sha256"],
            next(x["evidence_sha"] for x in state["stages"]
                 if x["name"] == "compare"),
        )

    def test_partial_primary_authenticated_adjudication_is_claimable(self):
        from app import golden_v2_review_routes as api
        fixture, state = self.set_up_review()
        comparison_sha = next(x["evidence_sha"] for x in state["stages"]
                              if x["name"] == "compare")
        body = api.AdjudicationReview(
            revision=state["revision"], source_sha256=fixture["source_sha"],
            comparison_sha256=comparison_sha, decisions=[],
            review_reference="mock-listening-only", confirm_listened=True,
            confirm=True,
        )
        # Mock the human listening operation: prove routing & claims only,
        # never pretend that automated tests constitute listening evidence.
        with patch.object(api, "_authorized_write_database",
                          return_value=(self.database, "reviewer", None)), \
             patch.object(api, "job_directory",
                          return_value=fixture["job_dir"]), \
             patch.object(api, "complete_adjudication_or_hold",
                          side_effect=lambda **kw: {
                              "stage": kw["claim"].get("stage", "validate_only"),
                              "status": "MOCK_ONLY_NOT_GOLDEN",
                          }):
            result = api.review_adjudication(
                fixture["job_id"], body, authorization="test")
        self.assertEqual(result["stage"], "adjudication")
        self.assertEqual(
            next(x["state"] for x in self.ledger.status(fixture["job_id"])["stages"]
                 if x["name"] == "adjudication"),
            "RUNNING",
        )
        self.assertFalse((fixture["job_dir"]/"golden-v2"/"final.srt").exists())

    def test_partial_review_bundle_import_preserves_human_review_hold(self):
        from app.pipeline.golden_v2_review_bundle import (
            inspect_review_bundle, apply_review_bundle,
        )
        fixture, state = self.set_up_review()
        bundle = inspect_review_bundle(
            database=self.database, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"],
            normalized_audio=fixture["job_dir"] / "normalized.flac",
        )
        # Explicitly KEEP the first candidate unresolved; do not forge
        # listening confirmation or manufacture an automated Golden pass.
        bundle["review_decisions"] = [{
            "candidate_index": 0,
            "status": "REVIEW_REQUIRED",
        }]
        result = apply_review_bundle(
            database=self.database, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"],
            normalized_audio=fixture["job_dir"] / "normalized.flac",
            bundle=bundle, reviewer="offline-test",
            review_reference="explicit-unresolved-offline-review",
            confirm_listened=True,
        )
        self.assertNotEqual(result.get("safe_publish_allowed"), True)
        self.assertFalse((fixture["job_dir"]/"golden-v2"/"final.srt").exists())
        latest = self.ledger.status(fixture["job_id"])
        self.assertEqual(latest["status"], "NEEDS_REVIEW")
        self.assertEqual(
            next(x["state"] for x in latest["stages"]
                 if x["name"] == "gemini_primary"),
            "REVIEW_REQUIRED",
        )
        self.assertFalse(result["provider_calls_started"])
        self.assertFalse(result["drive_mutation_started"])

    def test_tampered_comparison_hash_rejected_before_review_claim(self):
        from app import golden_v2_review_routes as api
        from fastapi import HTTPException
        fixture, state = self.set_up_review()
        body = api.AdjudicationReview(
            revision=state["revision"], source_sha256=fixture["source_sha"],
            comparison_sha256="e"*64, decisions=[],
            review_reference="offline-negative", confirm_listened=True,
            confirm=True,
        )
        with patch.object(api, "_authorized_write_database",
                          return_value=(self.database,"reviewer",None)), \
             patch.object(api,"job_directory",
                          return_value=fixture["job_dir"]):
            with self.assertRaises(HTTPException) as caught:
                api.review_adjudication(
                    fixture["job_id"], body, authorization="test")
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(
            next(x["state"] for x in self.ledger.status(fixture["job_id"])["stages"]
                 if x["name"]=="adjudication"),
            "PENDING",
        )

    def _make_adjudication_review_hold(self):
        from app.pipeline.golden_v2_review_bundle import (
            inspect_review_bundle, apply_review_bundle,
        )
        fixture, _ = self.set_up_review()
        bundle = inspect_review_bundle(
            database=self.database, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"],
            normalized_audio=fixture["job_dir"]/"normalized.flac",
        )
        bundle["review_decisions"] = [{
            "candidate_index": 0, "status": "REVIEW_REQUIRED",
        }]
        apply_review_bundle(
            database=self.database, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"],
            normalized_audio=fixture["job_dir"]/"normalized.flac",
            bundle=bundle, reviewer="offline-review",
            review_reference="unresolved-test-case",
            confirm_listened=True,
        )
        state=self.ledger.status(fixture["job_id"])
        self.assertEqual(state["status"],"NEEDS_REVIEW")
        held=next(x for x in state["stages"] if x["name"]=="adjudication")
        self.assertEqual(held["state"],"REVIEW_REQUIRED")
        return fixture,state,held

    def test_partial_auth_review_can_resume_existing_review_hold(self):
        from app import golden_v2_review_routes as api
        fixture,state,held=self._make_adjudication_review_hold()
        comparison=next(x for x in state["stages"] if x["name"]=="compare")
        body=api.AdjudicationReview(
            revision=state["revision"],source_sha256=fixture["source_sha"],
            comparison_sha256=comparison["evidence_sha"],
            decisions=[],review_reference="second-offline-review",
            confirm_listened=True,confirm=True,
        )
        with patch.object(api,"_authorized_write_database",
                          return_value=(self.database,"reviewer",None)), \
             patch.object(api,"job_directory",return_value=fixture["job_dir"]), \
             patch.object(api,"complete_adjudication_or_hold",
                          side_effect=lambda **kw: {"stage":kw["claim"].get("stage"),
                                                     "status":"MOCK_ONLY_NOT_GOLDEN"}):
            response=api.review_adjudication(
                fixture["job_id"],body,authorization="test")
        self.assertEqual(response["stage"],"adjudication")
        after=self.ledger.status(fixture["job_id"])
        self.assertEqual(
            next(x["state"] for x in after["stages"]
                 if x["name"]=="adjudication"),"RUNNING")
        self.assertEqual(after["revision"],state["revision"]+2)
        with self.ledger._tx() as db:
            recorded=db.execute(
                "SELECT prior_evidence_sha FROM v2_review_resumptions "
                "WHERE job_id=? AND stage_name='adjudication'",
                (fixture["job_id"],)).fetchone()
        self.assertEqual(recorded["prior_evidence_sha"],
                         held["evidence_sha"])

    def test_partial_auth_review_rejects_tampered_previous_hold(self):
        from app import golden_v2_review_routes as api
        from fastapi import HTTPException
        fixture,state,held=self._make_adjudication_review_hold()
        comparison=next(x for x in state["stages"] if x["name"]=="compare")
        for path in (fixture["job_dir"]/"golden-v2").glob(
                "adjudication-review-*.json"):
            if hashlib.sha256(path.read_bytes()).hexdigest()==held["evidence_sha"]:
                path.write_text('{"tampered":true}')
        body=api.AdjudicationReview(
            revision=state["revision"],source_sha256=fixture["source_sha"],
            comparison_sha256=comparison["evidence_sha"],
            decisions=[],review_reference="negative-offline-review",
            confirm_listened=True,confirm=True,
        )
        with patch.object(api,"_authorized_write_database",
                          return_value=(self.database,"reviewer",None)), \
             patch.object(api,"job_directory",return_value=fixture["job_dir"]), \
             patch.object(api,"complete_adjudication_or_hold",
                          side_effect=lambda **kw: {"status":"MOCK_ONLY_NOT_GOLDEN"}):
            with self.assertRaises(HTTPException) as caught:
                api.review_adjudication(
                    fixture["job_id"],body,authorization="test")
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.ledger.status(fixture["job_id"])["revision"],
                         state["revision"])
        self.assertEqual(
            next(x["state"] for x in
                 self.ledger.status(fixture["job_id"])["stages"]
                 if x["name"]=="adjudication"),"REVIEW_REQUIRED")

    def test_unrecorded_rescue_never_opens_review_bundle(self):
        from app.pipeline.golden_v2_review_bundle import inspect_review_bundle
        fixture, _state = self.set_up_review()
        with self.ledger._tx() as db:
            db.execute(
                "DELETE FROM v2_review_resumptions "
                "WHERE job_id=? AND reviewer='trusted-operator-independent-rescue'",
                (fixture["job_id"],),
            )
        with self.assertRaises(V2Conflict):
            inspect_review_bundle(
                database=self.database, job_id=fixture["job_id"],
                job_dir=fixture["job_dir"],
                normalized_audio=fixture["job_dir"] / "normalized.flac",
            )
