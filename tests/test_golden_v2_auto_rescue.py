"""Auto-rescue tests: verified SAFETY refusal -> paid claim -> partial compare.

Covers DESIGN.md section 8: eight unit tests plus one simulated-refusal
end-to-end test, plus the paid-side hook's spawn/cooling-off/state guards.
All fixture/mock based; no provider is contacted and no real cost is charged.

The fixture is a de-identified Liu Yunzhong-shaped job: 7,702.464 seconds,
three Gemini windows (0-2700s STOP, 2690-5390s STOP, 5380-7702.464s SAFETY
refusal), approved 343c, Gemini actual 225c, Chirp estimate 43c. 1,540
synthetic single-character Chirp words, one every 5 seconds, stand in for
the recovered word stream; 464 of them start inside the refused window and
split into 20 bounded Chirp-authoritative review pieces (19 x 24 + 8).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_auto_rescue import inspect_auto_rescue_eligibility
from app.pipeline.golden_v2_coordinator import advance_local, run_ready_local
from app.pipeline.golden_v2_partial_primary import (
    build_partial_primary, partial_primary_coverage,
)
from app.pipeline.golden_v2_primary_rescue import claim_independent_rescue
from app.pipeline.golden_v2_stage_runner import (
    _build_comparison_report, _bound_inputs, complete_adjudication_or_hold,
)
from app.pipeline.golden_v2_worker import run_v2_worker_pass
from app.providers.golden_v2_final_gate import validate_golden_v2
from scripts.golden_v2_auto_rescue_hook import (
    AUTO_RESCUE_REFERENCE, run_auto_rescue_hook_cycle,
)

CHARS = "甲乙丙丁戊己庚辛壬癸"
WINDOWS = [(0, 2700.0, "STOP"), (2690.0, 5390.0, "STOP"),
           (5380.0, 7702.464, "SAFETY")]
DURATION_MS = 7_702_464
WORD_COUNT = 1540
REFUSED_WORD_COUNT = 464
EXPECTED_PIECES = 20
GEMINI_ACTUAL_CENTS = 225
CHIRP_ACTUAL_CENTS = 43


def _wire(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


_SOURCE_COUNTER = itertools.count()


def build_held_job(root, ledger, *, block_reason="SAFETY", chirp_estimate=43,
                   approved=343, gemini_estimate=300,
                   refused_window_index=2):
    """Build a NEEDS_REVIEW job held by a verified refusal, revision 2.

    Window texts are slices of the in-scope Chirp word letters so the
    comparable scope aligns exactly (a fixture simplification: the real
    second window's text would also cover the 10s overlap with window 0).
    """
    # Each fixture needs a distinct source: the ledger refuses a second
    # approved job for the same source SHA without an explicit repeat flag.
    source = ("liu-yunzhong-test-audio-%d"
              % next(_SOURCE_COUNTER)).encode()
    sha = hashlib.sha256(source).hexdigest()
    job = ledger.create(
        source_sha=sha, mode="FLASH_CHIRP",
        estimated_stage_cents={"gemini_primary": gemini_estimate,
                               "chirp_independent": chirp_estimate,
                               "compare": 0, "third_optional": 0,
                               "adjudication": 0, "golden_qa": 0})
    ledger.approve(job, revision=0, approved_cents=approved,
                   reference="owner-test", actor="owner-test")
    job_dir = root / "jobs" / job
    job_dir.mkdir(parents=True)
    (job_dir / "normalized.flac").write_bytes(source)
    words = [{"word": CHARS[i % len(CHARS)], "start_ms": i * 5000,
              "end_ms": i * 5000 + 3000} for i in range(WORD_COUNT)]
    letters = "".join(word["word"] for word in words)
    texts = [
        "".join(word["word"] for word in words
                if int(start * 1000) <= word["start_ms"] < int(end * 1000))
        if index != refused_window_index else ""
        for index, (start, end, _) in enumerate(WINDOWS)
    ]
    (job_dir / "gemini-primary").mkdir()
    evidence = []
    for index, (start, end, _) in enumerate(WINDOWS):
        kind = "SAFETY" if index == refused_window_index else "STOP"
        identity = {"source_sha256": sha, "model": "gemini-3.8-flash",
                    "window": {"index": index, "start_seconds": start,
                               "end_seconds": end,
                               "gcs_uri": f"gs://test-bucket/window-{index}.flac",
                               "mime_type": "audio/flac"}}
        usage = {"prompt_token_count": 1_000_000,
                 "candidates_token_count": 0, "thoughts_token_count": 0}
        raw = {"identity": identity,
               "status": "COMPLETE" if kind == "STOP" else "REQUIRES_REVIEW",
               "finish_reason": kind if kind == "STOP" else "",
               "text": texts[index],
               "usage_metadata": usage,
               "raw_response": {"prompt_feedback": {
                   "block_reason": "" if kind == "STOP" else block_reason}}}
        raw_bytes = _wire(raw)
        (job_dir / "gemini-primary" / f"window-{index}.json").write_bytes(raw_bytes)
        evidence.append({"identity": identity,
                         "raw_audit_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                         "usage_metadata": usage,
                         "finish_reason": "STOP" if kind == "STOP" else ""})
    assembled = {"text": "\n\n".join(texts), "window_count": 3,
                 "structural_coverage": "INCOMPLETE",
                 "acoustic_coverage": "UNVERIFIED",
                 "review_items": [{"reason": "known_provider_response_requires_review",
                                   "window": 2}],
                 "ready_for_chirp_comparison": False, "golden_approved": False}
    report = {"schema": "golden-v2-primary-stage-v1", "job_id": job,
              "source_sha256": sha, "source_lineage_sha256": sha,
              "mode": "gemini_3_8_audio_first", "window_evidence": evidence,
              "assembled": assembled, "estimated_cents": gemini_estimate,
              "actual_cents": GEMINI_ACTUAL_CENTS, "ready_for_chirp": False,
              "golden_approved": False}
    report_bytes = _wire(report)
    (job_dir / "golden-v2").mkdir()
    (job_dir / "golden-v2" / "primary-stage.json").write_bytes(report_bytes)
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    claim = ledger.claim_next(job, worker_id="fake-provider-primary")
    ledger.hold_for_review(claim, evidence_sha=report_sha,
                           actual_cents=GEMINI_ACTUAL_CENTS)
    return {"job_id": job, "job_dir": job_dir, "source_sha": sha,
            "words": words, "report_sha": report_sha, "approved": approved,
            "chirp_estimate": chirp_estimate}


def claim_rescue(fixture, ledger, *, cap_cents=None, revision=2):
    """The mock paid claim: the real claim function, stubbed authorization."""
    return claim_independent_rescue(
        ledger=ledger, job_id=fixture["job_id"], job_dir=fixture["job_dir"],
        revision=revision, source_sha=fixture["source_sha"],
        cap_cents=fixture["approved"] if cap_cents is None else cap_cents,
        reference="test-auto-rescue",
        verify_authorization_and_live_source=lambda: True)


def complete_chirp(fixture, ledger, chirp_claim, *, actual_cents=CHIRP_ACTUAL_CENTS):
    """Stand in for the provider Chirp submit/recover with fixture words."""
    merged = {"schema": "golden-v2-chirp-merged-v1",
              "job_id": fixture["job_id"],
              "source_sha": fixture["source_sha"], "duration_ms": DURATION_MS,
              "timing_authority": "chirp_3", "words": fixture["words"]}
    merged_bytes = _wire(merged)
    (fixture["job_dir"] / "golden-v2" / "chirp-merged.json").write_bytes(merged_bytes)
    ledger.complete(chirp_claim,
                    evidence_sha=hashlib.sha256(merged_bytes).hexdigest(),
                    actual_cents=actual_cents)
    return merged


class _AutoRescueCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "artifacts"
        self.root.mkdir()
        self.database = self.root / "ledger.sqlite"
        self.ledger = GoldenV2Ledger(self.database)

    def held(self, **kwargs):
        return build_held_job(self.root, self.ledger, **kwargs)


class AutoRescueEligibilityTests(_AutoRescueCase):
    def test_safety_refusal_is_eligible(self):
        fixture = self.held()
        outcome = inspect_auto_rescue_eligibility(
            ledger=self.ledger, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"])
        self.assertTrue(outcome["eligible"])
        self.assertEqual(outcome["reason"], "VERIFIED_SAFETY_REFUSAL")
        self.assertEqual(outcome["revision"], 2)
        self.assertEqual(outcome["approved_cents"], 343)
        self.assertEqual(outcome["primary_actual_cents"], GEMINI_ACTUAL_CENTS)

    def test_unknown_refusal_outcome_is_not_eligible(self):
        fixture = self.held(block_reason="OTHER")
        outcome = inspect_auto_rescue_eligibility(
            ledger=self.ledger, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"])
        self.assertFalse(outcome["eligible"])
        self.assertEqual(outcome["reason"], "PRIMARY_REFUSAL_NOT_VERIFIED")
        self.assertEqual(self.ledger.status(fixture["job_id"])["status"],
                         "NEEDS_REVIEW")

    def test_repeat_claim_is_refused(self):
        fixture = self.held()
        claim_rescue(fixture, self.ledger)
        with self.assertRaises(V2Conflict):
            claim_rescue(fixture, self.ledger)
        self.assertFalse(inspect_auto_rescue_eligibility(
            ledger=self.ledger, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"])["eligible"])

    def test_budget_overrun_refuses_claim(self):
        fixture = self.held()
        # Aggregate trial exposure: a second approved job reserves 500c, so
        # this job's 343c exposure no longer fits under a 343c cap even
        # though the job's own budget (225c actual + 43c Chirp) fits.
        other = self.ledger.create(
            source_sha=hashlib.sha256(b"other-test-audio").hexdigest(),
            mode="FLASH_CHIRP",
            estimated_stage_cents={"gemini_primary": 300,
                                   "chirp_independent": 200, "compare": 0,
                                   "third_optional": 0, "adjudication": 0,
                                   "golden_qa": 0})
        self.ledger.approve(other, revision=0, approved_cents=500,
                            reference="owner-test", actor="owner-test")
        with self.assertRaises(V2Conflict):
            claim_rescue(fixture, self.ledger)
        # A cap below the job's own approved amount is refused as well.
        with self.assertRaises(V2Conflict):
            claim_rescue(fixture, self.ledger, cap_cents=342)
        self.assertEqual(self.ledger.status(fixture["job_id"])["status"],
                         "NEEDS_REVIEW")

    def test_worker_pass_is_readonly_and_provider_free(self):
        fixture = self.held()
        before = self.ledger.status(fixture["job_id"])
        result = run_v2_worker_pass(database=self.database, artifact_root=self.root)
        self.assertEqual(result["action"], "WORKER_CYCLE_COMPLETE")
        self.assertFalse(result["provider_calls_started"])
        self.assertFalse(result["drive_mutation_started"])
        self.assertFalse(result["gcs_mutation_started"])
        (outcome,) = result["outcomes"]
        self.assertEqual(outcome["job_id"], fixture["job_id"])
        self.assertEqual([item["action"] for item in outcome["actions"]],
                         ["BLOCKED"])
        self.assertTrue(outcome["auto_rescue"]["eligible"])
        self.assertEqual(outcome["auto_rescue"]["reason"],
                         "VERIFIED_SAFETY_REFUSAL")
        self.assertEqual(self.ledger.status(fixture["job_id"]), before)


class AutoRescueHookTests(_AutoRescueCase):
    def _stub(self, marker):
        stub = Path(self.temp.name) / "stub_rescue.py"
        stub.write_text(
            "import json, sys\n"
            f"open({str(marker)!r}, 'a').write("
            "json.dumps(sys.argv[1:]) + chr(10))\n", encoding="utf-8")
        return stub

    def _spawned_argv(self, marker, expected=1, timeout_seconds=5.0):
        # Detached spawns start asynchronously; wait for all expected lines
        # instead of returning on the first one.
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if marker.exists():
                lines = marker.read_text(encoding="utf-8").splitlines()
                if len(lines) >= expected:
                    return [json.loads(line) for line in lines]
            time.sleep(0.05)
        self.fail("stub rescue script was not spawned")

    def test_hook_spawns_rescue_once_with_operator_arguments(self):
        fixture = self.held()
        marker = Path(self.temp.name) / "spawned-argv.jsonl"
        now = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
        result = run_auto_rescue_hook_cycle(
            database=self.database, artifact_root=self.root,
            rescue_script=self._stub(marker), now=now)
        self.assertEqual(result["action"], "AUTO_RESCUE_HOOK_CYCLE_COMPLETE")
        self.assertEqual(len(result["spawned"]), 1)
        (spawned,) = result["spawned"]
        self.assertEqual(spawned["job_id"], fixture["job_id"])
        self.assertEqual(spawned["revision"], 2)
        self.assertEqual(spawned["cap_cents"], 343)
        (argv,) = self._spawned_argv(marker)
        self.assertEqual(argv[-1], "--submit")
        self.assertEqual(argv[argv.index("--job-id") + 1], fixture["job_id"])
        self.assertEqual(argv[argv.index("--source-sha") + 1],
                         fixture["source_sha"])
        self.assertEqual(argv[argv.index("--revision") + 1], "2")
        self.assertEqual(argv[argv.index("--cap-cents") + 1], "343")
        self.assertEqual(argv[argv.index("--reference") + 1], AUTO_RESCUE_REFERENCE)
        state = json.loads((self.root / "auto-rescue-hook-state.json").read_text())
        self.assertEqual(state["jobs"][fixture["job_id"]]["attempts"], 1)

    def test_hook_never_retries_uncertain_paid_launch_after_cooldown(self):
        fixture = self.held()
        marker = Path(self.temp.name) / "spawned-argv.jsonl"
        stub = self._stub(marker)
        start = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)

        def cycle(offset_seconds):
            return run_auto_rescue_hook_cycle(
                database=self.database, artifact_root=self.root,
                rescue_script=stub, retry_interval_seconds=3600,
                now=start + timedelta(seconds=offset_seconds))

        self.assertEqual(len(cycle(0)["spawned"]), 1)
        for offset in (0, 3700, 7400, 11100):
            held = cycle(offset)
            self.assertEqual(held["spawned"], [])
            self.assertEqual(
                [item["reason"] for item in held["deferred"]],
                ["PRIOR_LAUNCH_RECONCILIATION_REQUIRED"])
        state = json.loads((self.root / "auto-rescue-hook-state.json").read_text())
        self.assertEqual(state["jobs"][fixture["job_id"]]["attempts"], 1)
        self.assertEqual(
            state["jobs"][fixture["job_id"]]["phase"],
            "PROCESS_LAUNCHED_RECONCILE_LEDGER")
        self.assertEqual(len(self._spawned_argv(marker, expected=1)), 1)

    def test_hook_ignores_ineligible_jobs(self):
        self.held(block_reason="OTHER")
        marker = Path(self.temp.name) / "spawned-argv.jsonl"
        result = run_auto_rescue_hook_cycle(
            database=self.database, artifact_root=self.root,
            rescue_script=self._stub(marker),
            now=datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(result["action"], "AUTO_RESCUE_HOOK_CYCLE_COMPLETE")
        self.assertEqual(result["spawned"], [])
        self.assertEqual(result["deferred"], [])
        self.assertFalse(marker.exists())

    def test_hook_rejects_corrupt_state(self):
        self.held()
        (self.root / "auto-rescue-hook-state.json").write_text(
            "not json", encoding="utf-8")
        marker = Path(self.temp.name) / "spawned-argv.jsonl"
        with self.assertRaises(V2Conflict):
            run_auto_rescue_hook_cycle(
                database=self.database, artifact_root=self.root,
                rescue_script=self._stub(marker),
                now=datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc))
        self.assertFalse(marker.exists())


class PartialCompareTests(_AutoRescueCase):
    def rescued(self, **kwargs):
        fixture = self.held(**kwargs)
        chirp_claim = claim_rescue(fixture, self.ledger)
        merged = complete_chirp(fixture, self.ledger, chirp_claim)
        return fixture, merged

    def test_partial_compare_allowed_and_marked(self):
        fixture, _ = self.rescued()
        actions = run_ready_local(ledger=self.ledger, job_id=fixture["job_id"],
                                  job_dir=fixture["job_dir"])
        self.assertEqual([item["action"] for item in actions],
                         ["COMPARISON_COMPLETE", "HUMAN_AUDIO_REVIEW_REQUIRED"])
        report = json.loads((fixture["job_dir"] / "golden-v2"
                             / "engine-comparison.json").read_text())
        self.assertTrue(report["partial_primary"])
        self.assertEqual(report["refused_windows"],
                         [{"index": 2, "start_seconds": 5380.0,
                           "end_seconds": 7702.464, "block_reason": "SAFETY"}])
        self.assertEqual(report["comparison_scope_seconds"], 5390.0)
        self.assertEqual(report["comparison_scope"],
                         "GEMINI_SUCCESS_WINDOWS_TIME_SCOPED")
        self.assertEqual(report["pending_review_count"], EXPECTED_PIECES + 2)
        self.assertEqual(report["comparable_window_count"], 2)
        self.assertEqual(len(report["unresolved_seams"]), 2)
        self.assertEqual(report["alignment_health"]["status"], "PASS")
        self.assertEqual(report["alignment_health"]["primary_coverage_ratio"], 1.0)
        pieces = [item for item in report["candidates"]
                  if item["kind"] == "CHIRP_AUTHORITATIVE_REFUSED_SPAN"]
        seam_candidates = [item for item in report["candidates"]
                           if item["kind"] == "GEMINI_OVERLAP_SEAM_REVIEW"]
        self.assertEqual(len(seam_candidates), 2)
        self.assertEqual(len(pieces), EXPECTED_PIECES)
        self.assertFalse(report["exact_normalized_match"])
        self.assertTrue(all(item["comparison_status"] == "REVIEW_REQUIRED"
                            and item["chirp_authoritative"] is True
                            and item["gemini_crosscheck"] == "absent_safety_refusal"
                            and item["auto_replacement_allowed"] is False
                            for item in pieces))
        self.assertEqual(pieces[0]["source_time_start"], 5_390_000)
        self.assertEqual(pieces[-1]["source_time_end"], 7_698_000)
        self.assertEqual(len(report["chirp_authoritative_spans"]), EXPECTED_PIECES)
        partial_path = (fixture["job_dir"] / "golden-v2"
                        / "primary-stage-partial.json")
        partial = json.loads(partial_path.read_text())
        self.assertEqual(partial["schema"], "golden-v2-primary-stage-partial-v1")
        self.assertEqual(partial["original_primary_evidence_sha256"],
                         fixture["report_sha"])
        # The persisted file carries no self-digest; the report binds it by
        # the file's own SHA-256.
        self.assertEqual(hashlib.sha256(partial_path.read_bytes()).hexdigest(),
                         report["partial_primary_evidence_sha256"])
        self.assertFalse((fixture["job_dir"] / "golden-v2" / "final.srt").exists())
        stages = {row["name"]: row
                  for row in self.ledger.status(fixture["job_id"])["stages"]}
        self.assertEqual(stages["gemini_primary"]["state"], "REVIEW_REQUIRED")
        self.assertEqual(stages["compare"]["state"], "COMPLETE")
        self.assertEqual(stages["adjudication"]["state"], "PENDING")
        self.assertEqual(
            hashlib.sha256((fixture["job_dir"] / "golden-v2"
                            / "primary-stage.json").read_bytes()).hexdigest(),
            fixture["report_sha"])

    def test_middle_safety_refusal_keeps_later_successful_window(self):
        fixture, _ = self.rescued(refused_window_index=1)
        actions = run_ready_local(
            ledger=self.ledger, job_id=fixture["job_id"],
            job_dir=fixture["job_dir"])
        self.assertEqual(
            [item["action"] for item in actions],
            ["COMPARISON_COMPLETE", "HUMAN_AUDIO_REVIEW_REQUIRED"])
        report = json.loads(
            (fixture["job_dir"] / "golden-v2" /
             "engine-comparison.json").read_text())
        partial = json.loads(
            (fixture["job_dir"] / "golden-v2" /
             "primary-stage-partial.json").read_text())
        self.assertEqual(partial["covered_window_indices"], [0, 2])
        self.assertEqual(report["comparable_window_count"], 2)
        self.assertEqual(
            report["comparison_scope"], "GEMINI_SUCCESS_WINDOWS_TIME_SCOPED")
        self.assertGreater(report["comparison_scope_seconds"], 5000)
        self.assertEqual(report["alignment_health"]["status"], "PASS")
        self.assertEqual(report["unaligned_long_spans"], 0)
        self.assertEqual(len(report["unresolved_seams"]), 2)
        self.assertFalse(report["exact_normalized_match"])
        self.assertFalse((fixture["job_dir"] / "golden-v2" / "final.srt").exists())

    def test_partial_compare_requires_recorded_rescue(self):
        fixture = self.held()
        # Chirp evidence exists but no rescue was ever claimed: the route
        # must stay closed at every layer.
        merged = {"schema": "golden-v2-chirp-merged-v1",
                  "job_id": fixture["job_id"],
                  "source_sha": fixture["source_sha"], "duration_ms": DURATION_MS,
                  "timing_authority": "chirp_3", "words": fixture["words"]}
        merged_bytes = _wire(merged)
        (fixture["job_dir"] / "golden-v2" / "chirp-merged.json").write_bytes(merged_bytes)
        merged_sha = hashlib.sha256(merged_bytes).hexdigest()
        with self.ledger._tx() as db:
            db.execute(
                "UPDATE v2_stages SET state='COMPLETE',evidence_sha=?,"
                "actual_cents=? WHERE job_id=? AND name='chirp_independent'",
                (merged_sha, CHIRP_ACTUAL_CENTS, fixture["job_id"]))
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(fixture["job_id"],
                                   worker_id="cannot-bypass-review")
        with self.assertRaises(V2Conflict):
            _bound_inputs(
                ledger=self.ledger,
                claim={"job_id": fixture["job_id"], "stage": "compare",
                       "claim": "unused", "source_sha": fixture["source_sha"],
                       "estimate_cents": 0},
                job_dir=fixture["job_dir"])
        blocked = advance_local(ledger=self.ledger, job_id=fixture["job_id"],
                                job_dir=fixture["job_dir"])
        self.assertEqual(blocked["action"], "BLOCKED")
        self.assertEqual(blocked["reason"], "NEEDS_REVIEW")

    def test_partial_coverage_threshold_still_applies_within_scope(self):
        fixture, merged = self.rescued()
        partial = build_partial_primary(ledger=self.ledger,
                                        job_id=fixture["job_id"],
                                        job_dir=fixture["job_dir"])
        self.assertEqual(partial_primary_coverage(partial), 1.0)
        compare_claim = self.ledger.claim_next(
            fixture["job_id"], worker_id="coverage-test")
        self.assertEqual(compare_claim["stage"], "compare")
        stages = {row["name"]: row
                  for row in self.ledger.status(fixture["job_id"])["stages"]}
        thin = dict(partial, covered_span_seconds=5000.0)
        with self.assertRaisesRegex(V2Conflict, "alignment is not trustworthy"):
            _build_comparison_report(
                primary_text=partial["assembled_text"], merged=merged,
                primary=thin, claim=compare_claim, stages=stages,
                job_dir=fixture["job_dir"])

    def test_partial_compare_still_refuses_golden_pass_without_acoustic_evidence(self):
        fixture, _ = self.rescued()
        actions = run_ready_local(ledger=self.ledger, job_id=fixture["job_id"],
                                  job_dir=fixture["job_dir"])
        self.assertEqual(actions[-1]["action"], "HUMAN_AUDIO_REVIEW_REQUIRED")
        v2dir = fixture["job_dir"] / "golden-v2"
        self.assertFalse((v2dir / "final.srt").exists())
        self.assertFalse((v2dir / "final-corrected-transcript.txt").exists())
        self.assertFalse((v2dir / "golden-qa.json").exists())
        report = json.loads((v2dir / "engine-comparison.json").read_text())
        srt = ("1\n00:00:00,000 --> 00:00:08,000\n復次\n\n"
               "2\n00:00:08,000 --> 00:00:12,000\n聞如是\n")
        gate = validate_golden_v2(
            srt=srt, transcript="復次聞如是",
            chirp_word_boundaries_ms={0, 8000, 12000},
            acoustic_gate="UNVERIFIED", semantic_gate="NOT_DETERMINED",
            comparison=report, adjudications=[])
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertIn("acoustic_completeness_not_passed", gate["errors"])
        self.assertIn("unresolved_comparison_candidate:0", gate["errors"])
        claim = self.ledger.claim_next(fixture["job_id"],
                                       worker_id="test-adjudication")
        self.assertEqual(claim["stage"], "adjudication")
        with self.assertRaises(V2Conflict):
            complete_adjudication_or_hold(ledger=self.ledger, claim=claim,
                                          job_dir=fixture["job_dir"])


class AutoRescueEndToEndTests(_AutoRescueCase):
    def test_liu_like_job_runs_worker_to_adjudication(self):
        fixture = self.held()
        first = run_v2_worker_pass(database=self.database, artifact_root=self.root)
        (first_outcome,) = first["outcomes"]
        self.assertEqual([item["action"] for item in first_outcome["actions"]],
                         ["BLOCKED"])
        self.assertTrue(first_outcome["auto_rescue"]["eligible"])
        # Mock paid claim (real claim function, stubbed authorization) and
        # mock Chirp submit/recover with the fixture word stream.
        chirp_claim = claim_rescue(fixture, self.ledger)
        complete_chirp(fixture, self.ledger, chirp_claim)
        second = run_v2_worker_pass(database=self.database, artifact_root=self.root)
        self.assertFalse(second["provider_calls_started"])
        (second_outcome,) = second["outcomes"]
        self.assertEqual([item["action"] for item in second_outcome["actions"]],
                         ["COMPARISON_COMPLETE", "HUMAN_AUDIO_REVIEW_REQUIRED"])
        report = json.loads((fixture["job_dir"] / "golden-v2"
                             / "engine-comparison.json").read_text())
        self.assertTrue(report["partial_primary"])
        self.assertEqual(report["pending_review_count"], EXPECTED_PIECES + 2)
        self.assertFalse((fixture["job_dir"] / "golden-v2" / "final.srt").exists())
        stages = {row["name"]: row
                  for row in self.ledger.status(fixture["job_id"])["stages"]}
        self.assertEqual(stages["gemini_primary"]["state"], "REVIEW_REQUIRED")
        self.assertEqual(stages["compare"]["state"], "COMPLETE")
        self.assertEqual(stages["adjudication"]["state"], "PENDING")
        self.assertEqual(stages["golden_qa"]["state"], "PENDING")


if __name__ == "__main__":
    unittest.main()
