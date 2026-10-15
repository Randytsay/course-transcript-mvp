"""Provider-free regression tests for the local clock fix path.

These tests assert that the local clock fix:

* does **not** touch the original ``provider-raw.json`` (its SHA-256 is
  preserved across propose and apply);
* does **not** call the Speech gateway a second time;
* does **not** change the accounted ``actual_cents``;
* does **not** re-promote windows that were not quarantined (the existing
  six DONE windows in the c8f1.. fixture must stay DONE, untouched);
* does **not** silently drop a word it cannot fix; the unresolvable word is
  surfaced in the proposal for human re-listen.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_chirp_local_clock_fix import (
    apply_local_clock_fix, parse_local_clock_fix_candidate,
    propose_local_clock_fix,
)
from app.pipeline.golden_v2_chirp_recovery_worker import run_chirp_recovery_pass
from app.pipeline.golden_v2_chirp_stage import (
    plan_approved_chirp, recover_chirp_window, submit_planned_chirp,
)
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_source_lineage import bind_source_lineage
from app.providers.golden_v2_chirp_gateway import ChirpV2Gateway
from tests.test_golden_v2_chirp_stage import (
    ChirpStageIntegrationTests, FakeResult, ResultCloud, SpeechFake,
    passing_acoustic_inspector, speech_words,
)


def _forced_words(*words):
    """Render a Speech V2 result with explicit zero-durations on purpose.

    ``forced`` here means we have full control over every offset, including
    intentionally bad ones (e.g. inverted or out-of-window).
    """
    return json.dumps({"results": [{"alternatives": [{"words": [
        {"word": text, "startOffset": f"{start:.3f}s", "endOffset": f"{end:.3f}s"}
        for text, start, end in words
    ]}]}]}, ensure_ascii=False).encode()


def _repairable_result() -> bytes:
    """Invalid end for 好, followed by a provider-verified same-start end."""
    return json.dumps({"results":[{"alternatives":[{"words":[
        {"word":"前","startOffset":"0.000s","endOffset":"0.100s"},
        {"word":"你","startOffset":"0.100s","endOffset":"0.300s"},
        {"word":"好","startOffset":"0.300s","endOffset":"0.200s"},
        {"word":"嗎","startOffset":"0.300s","endOffset":"0.600s"},
        {"word":"喔","startOffset":"0.600s","endOffset":"0.900s"},
    ]}]}]},ensure_ascii=False).encode()


class ParseLocalClockFixCandidateTests(unittest.TestCase):
    def test_clean_window_keeps_every_word_without_resolution(self):
        window = {"window_id": "w0", "start_ms": 0, "end_ms": 5_000}
        candidate = parse_local_clock_fix_candidate(
            _forced_words(("你", 0, 0.2), ("好", 0.2, 0.4)), window)
        self.assertEqual(candidate["fixable_word_count"], 0)
        self.assertEqual(candidate["unresolvable_word_count"], 0)
        self.assertEqual(candidate["fixed_words"], [
            {"word": "你", "start_ms": 0, "end_ms": 200},
            {"word": "好", "start_ms": 200, "end_ms": 400},
        ])

    def test_inverted_end_coalesces_only_with_identical_provider_start(self):
        window = {"window_id":"w0","start_ms":0,"end_ms":5_000}
        candidate = parse_local_clock_fix_candidate(_repairable_result(), window)
        self.assertTrue(candidate["safe_to_apply"])
        self.assertEqual(candidate["unresolvable"], [])
        self.assertEqual("前你好嗎喔", "".join(x["word"] for x in candidate["fixed_words"]))
        self.assertEqual(
            candidate["fixed_words"][2]["timing_resolution"],
            "COALESCED_SAME_START_PROVIDER_END")
        self.assertEqual(candidate["fixed_words"][2]["end_ms"],600)

    def test_missing_end_coalesces_only_with_identical_start(self):
        payload = json.dumps({"results":[{"alternatives":[{"words":[
            {"word":"前","startOffset":"0.000s","endOffset":"0.100s"},
            {"word":"你","startOffset":"0.100s","endOffset":"0.200s"},
            {"word":"好","startOffset":"0.300s"},
            {"word":"嗎","startOffset":"0.300s","endOffset":"0.500s"},
            {"word":"喔","startOffset":"0.500s","endOffset":"0.800s"},
        ]}]}]},ensure_ascii=False).encode()
        candidate = parse_local_clock_fix_candidate(
            payload, {"window_id":"w0","start_ms":0,"end_ms":5_000})
        self.assertTrue(candidate["safe_to_apply"])
        self.assertEqual("前你好嗎喔", "".join(x["word"] for x in candidate["fixed_words"]))
        fixed=next(x for x in candidate["fixed_words"] if x["word"]=="好嗎")
        self.assertEqual(fixed["end_ms"],500)
        self.assertEqual(fixed["timing_resolution"],"COALESCED_SAME_START_PROVIDER_END")

    def test_out_of_window_word_requires_human_review(self):
        window = {"window_id":"w0","start_ms":0,"end_ms":1_000}
        candidate=parse_local_clock_fix_candidate(
            _forced_words(("近",0.1,0.3),("遠",5,5.2)),window)
        self.assertFalse(candidate["safe_to_apply"])
        self.assertEqual(candidate["unresolvable"][0]["reason"],
                         "start out of source window")
        self.assertEqual([w["word"] for w in candidate["fixed_words"]],["近"])

    def test_over_fixed_fraction_is_rejected(self):
        # 2 of 3 entries are unresolvable ⇒ 2/3 > 0.50 → reject.
        window = {"window_id": "w0", "start_ms": 0, "end_ms": 1_000}
        with self.assertRaisesRegex(V2Conflict, "manual review required"):
            parse_local_clock_fix_candidate(
                _forced_words(
                    ("遠", 6, 6.2),  # start out of window
                    ("更遠", 7, 7.2),  # start out of window
                    ("好", 0.4, 0.5)),
                window)


class ApplyLocalClockFixTests(ChirpStageIntegrationTests):
    """Reuse the parent setUp so we get a real FLAC + ledger + speech fake."""

    def _stage_a_window(self, *words):
        self.plan(max_window_seconds=10)
        rows = submit_planned_chirp(
            ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        row = rows[0]
        prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
        raw = words[0] if len(words) == 1 and isinstance(words[0], bytes) else _forced_words(*words)
        self.cloud.results[prefix + "out.json"] = FakeResult(
            prefix + "out.json", raw)
        self.speech.operations[row["operation_name"]].done = True
        return row

    def test_apply_recovers_quarantined_window_without_provider_call(self):
        row = self._stage_a_window(_repairable_result())
        self.assertEqual(
            recover_chirp_window(
                ledger=self.ledger, job_id=self.job_id, window_id=row["window_id"],
                job_dir=self.job_dir, gateway=self.gateway,
                cost_accountant=lambda _: 2),
            "REVIEW_REQUIRED")
        raw_path = (self.job_dir / "golden-v2" / "chirp-evidence"
                    / row["window_id"] / "provider-raw.json")
        raw_sha_before = file_sha256(raw_path)
        speech_calls_before = len(self.speech.calls)
        cents_before = self.ledger.chirp_windows(self.job_id)[0]["actual_cents"]

        proposal = propose_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir)
        self.assertEqual(proposal["window_id"], row["window_id"])
        self.assertEqual(proposal["raw_sha"], raw_sha_before)
        self.assertEqual(proposal["submit_authorized"], False)

        applied = apply_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            proposal_sha=proposal["proposal_sha256"], owner_token="owner-test")
        self.assertEqual(applied["action"], "LOCAL_CLOCK_FIX_APPLIED")
        # Original raw JSON is byte-identical after the fix.
        self.assertEqual(file_sha256(raw_path), raw_sha_before)
        # No new Speech POST.
        self.assertEqual(len(self.speech.calls), speech_calls_before)
        # No charge change.
        after = self.ledger.chirp_windows(self.job_id)[0]
        self.assertEqual(after["status"], "DONE")
        self.assertEqual(after["actual_cents"], cents_before)
        self.assertEqual(after["local_clock_fix_sha"], proposal["proposal_sha256"])
        # window.json references both SHAs.
        win = json.loads((self.job_dir / "golden-v2" / "chirp-evidence"
                          / row["window_id"] / "window.json").read_bytes())
        self.assertEqual(win["raw_sha"], raw_sha_before)
        self.assertEqual(win["local_clock_fix_sha"], proposal["proposal_sha256"])

    def test_apply_rejects_tampered_raw_bytes(self):
        row = self._stage_a_window(_repairable_result())
        recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=row["window_id"],
            job_dir=self.job_dir, gateway=self.gateway,
            cost_accountant=lambda _: 2)
        proposal = propose_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir)
        raw_path = (self.job_dir / "golden-v2" / "chirp-evidence"
                    / row["window_id"] / "provider-raw.json")
        raw_path.write_bytes(b'{"tampered": true}')
        with self.assertRaisesRegex(V2Conflict, "identity or cost evidence"):
            apply_local_clock_fix(
                ledger=self.ledger, job_id=self.job_id,
                window_id=row["window_id"], job_dir=self.job_dir,
                proposal_sha=proposal["proposal_sha256"], owner_token="owner-test")

    def test_apply_blocks_unresolved_word_without_losing_its_evidence(self):
        raw = _forced_words(
            ("前", 0.1, 0.2), ("中", 0.3, 0.5),
            ("越界", 10, 11), ("後", 0.6, 0.8),
        )
        row = self._stage_a_window(raw)
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            gateway=self.gateway, cost_accountant=lambda _: 2),
            "REVIEW_REQUIRED")
        proposal = propose_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir)
        self.assertFalse(proposal["safe_to_apply"])
        self.assertEqual(proposal["unresolvable_word_count"], 1)
        with self.assertRaisesRegex(V2Conflict, "human review"):
            apply_local_clock_fix(
                ledger=self.ledger, job_id=self.job_id,
                window_id=row["window_id"], job_dir=self.job_dir,
                proposal_sha=proposal["proposal_sha256"], owner_token="owner-test")
        stage = self.ledger.chirp_windows(self.job_id)[0]
        self.assertEqual(stage["status"], "REVIEW_REQUIRED")
        self.assertEqual(stage["actual_cents"], 2)

    def test_merge_rechecks_clock_fix_identity(self):
        row = self._stage_a_window(_repairable_result())
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            gateway=self.gateway, cost_accountant=lambda _: 2),
            "REVIEW_REQUIRED")
        proposal = propose_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir)
        apply_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            proposal_sha=proposal["proposal_sha256"], owner_token="owner-test")
        path = self.job_dir/"golden-v2"/"chirp-evidence"/row["window_id"]/"window.json"
        path.write_bytes(b'{"tampered":true}')
        from app.pipeline.golden_v2_chirp_stage import finish_approved_chirp
        with self.assertRaises(V2Conflict):
            finish_approved_chirp(ledger=self.ledger, job_id=self.job_id,
                                  job_dir=self.job_dir,
                                  acoustic_inspector=passing_acoustic_inspector)

    def test_apply_refuses_window_not_in_review(self):
        # A clean window that finishes DONE must NOT be re-promotable.
        self.plan(max_window_seconds=10)
        rows = submit_planned_chirp(
            ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        row = rows[0]
        prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.results[prefix + "out.json"] = FakeResult(
            prefix + "out.json", speech_words(("好", 0.1, 0.3)))
        self.speech.operations[row["operation_name"]].done = True
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            gateway=self.gateway, cost_accountant=lambda _: 2),
            "DONE")
        with self.assertRaisesRegex(V2Conflict, "only a quarantined"):
            apply_local_clock_fix(
                ledger=self.ledger, job_id=self.job_id,
                window_id=row["window_id"], job_dir=self.job_dir,
                proposal_sha="a" * 64, owner_token="owner-test")

    def test_existing_records_and_raw_sha_survive_local_fix(self):
        # Single-window fixture: verify evidence/cost preservation; multiwindow needs separate test.
        self.plan(max_window_seconds=10)
        rows = submit_planned_chirp(
            ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        # We only have one window in the plan fixture; simulate the "other
        # six are DONE" by recording their status outside the ledger: just
        # verify the recovery worker still considers the fixed window DONE
        # and that the original raw SHA survives an entire recovery pass.
        row = rows[0]
        prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.results[prefix + "out.json"] = FakeResult(
            prefix + "out.json", _repairable_result())
        self.speech.operations[row["operation_name"]].done = True
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            gateway=self.gateway, cost_accountant=lambda _: 2),
            "REVIEW_REQUIRED")
        raw_path = (self.job_dir / "golden-v2" / "chirp-evidence"
                    / row["window_id"] / "provider-raw.json")
        raw_sha_before = file_sha256(raw_path)
        proposal = propose_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir)
        apply_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            proposal_sha=proposal["proposal_sha256"], owner_token="owner-test")
        # Raw SHA unchanged through the whole fix+apply cycle.
        self.assertEqual(file_sha256(raw_path), raw_sha_before)
        # Window is now DONE; merge is possible if the rest were DONE.
        self.assertEqual(self.ledger.chirp_windows(self.job_id)[0]["status"],
                         "DONE")

    def test_ten_real_ledger_windows_clear_global_paid_hold_after_four_local_fixes(self):
        """10 submitted fake LROs: 6 DONE + 4 reviewed; complete old stage to unblock new job."""
        planned = self.plan(max_window_seconds=0.5, overlap_seconds=0.0,
                            estimate_window_cents=lambda _: 1)
        self.assertEqual(len(planned), 10)
        rows = submit_planned_chirp(ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(self.speech.calls), 10)
        unresolved_indices = {5, 7, 8, 9}
        repair_raw = json.dumps({"results": [{"alternatives": [{"words": [
            {"word":"前","startOffset":"0.000s","endOffset":"0.040s"},
            {"word":"你","startOffset":"0.040s","endOffset":"0.120s"},
            {"word":"好","startOffset":"0.120s","endOffset":"0.070s"},
            {"word":"嗎","startOffset":"0.120s","endOffset":"0.230s"},
            {"word":"後","startOffset":"0.230s","endOffset":"0.350s"},
        ]}]}]}, ensure_ascii=False).encode()
        for idx, row in enumerate(rows):
            self.speech.operations[row["operation_name"]].done = True
            prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
            raw = repair_raw if idx in unresolved_indices else speech_words(
                ("乾淨", 0.1, 0.35))
            self.cloud.results[prefix + "out.json"] = FakeResult(prefix + "out.json", raw)
            status = recover_chirp_window(
                ledger=self.ledger, job_id=self.job_id, window_id=row["window_id"],
                job_dir=self.job_dir, gateway=self.gateway,
                cost_accountant=lambda _: 1)
            self.assertEqual(status, "REVIEW_REQUIRED" if idx in unresolved_indices else "DONE")
        ledger_rows = self.ledger.chirp_windows(self.job_id)
        self.assertEqual(sum(r["status"] == "REVIEW_REQUIRED" for r in ledger_rows), 4)
        self.assertEqual(sum(r["status"] == "DONE" for r in ledger_rows), 6)
        old_good = {r["window_id"]: (r["result_sha"], r["actual_cents"])
                    for r in ledger_rows if r["status"] == "DONE"}
        new_id = self.ledger.create(
            source_sha="e" * 64, mode="FLASH_CHIRP",
            estimated_stage_cents=dict(gemini_primary=1, chirp_independent=1,
                                       compare=0, third_optional=0, adjudication=0,
                                       golden_qa=0))
        self.ledger.approve(new_id, revision=0, approved_cents=2,
                            reference="SECOND SOURCE TEST", actor="test")
        with self.assertRaisesRegex(V2Conflict, "unreconciled global"):
            self.ledger.claim_next(new_id, worker_id="new",
                                   aggregate_cap_cents=1000)
        for row in ledger_rows:
            if row["status"] != "REVIEW_REQUIRED":
                continue
            proposal = propose_local_clock_fix(
                ledger=self.ledger, job_id=self.job_id,
                window_id=row["window_id"], job_dir=self.job_dir)
            self.assertTrue(proposal["safe_to_apply"])
            result = apply_local_clock_fix(
                ledger=self.ledger, job_id=self.job_id,
                window_id=row["window_id"], job_dir=self.job_dir,
                proposal_sha=proposal["proposal_sha256"], owner_token="test-operator")
            self.assertEqual(result["action"], "LOCAL_CLOCK_FIX_APPLIED")
        self.assertEqual(sum(r["status"] == "DONE" for r in
                             self.ledger.chirp_windows(self.job_id)), 10)
        recovered = run_chirp_recovery_pass(
            ledger=self.ledger, job_id=self.job_id, job_dir=self.job_dir,
            gateway=self.gateway, cost_accountant=lambda _: 1,
            acoustic_inspector=passing_acoustic_inspector)
        self.assertEqual(recovered["action"], "CHIRP_COMPLETE")
        old_state = self.ledger.status(self.job_id)
        self.assertEqual(next(s["state"] for s in old_state["stages"]
                              if s["name"] == "chirp_independent"), "COMPLETE")
        for row in self.ledger.chirp_windows(self.job_id):
            if row["window_id"] in old_good:
                self.assertEqual((row["result_sha"], row["actual_cents"]),
                                 old_good[row["window_id"]])
        self.assertEqual(len(self.speech.calls), 10)
        next_claim = self.ledger.claim_next(new_id, worker_id="new",
                                            aggregate_cap_cents=1000)
        self.assertEqual(next_claim["stage"], "gemini_primary")
        self.assertEqual(self.ledger.status(new_id)["status"], "PROCESSING")

    def test_local_fix_followed_by_recovery_completes_stage(self):
        self.plan(max_window_seconds=10)
        rows = submit_planned_chirp(
            ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        row = rows[0]
        prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.results[prefix + "out.json"] = FakeResult(
            prefix + "out.json", _repairable_result())
        self.speech.operations[row["operation_name"]].done = True
        recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            gateway=self.gateway, cost_accountant=lambda _: 2)
        proposal = propose_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir)
        apply_local_clock_fix(
            ledger=self.ledger, job_id=self.job_id,
            window_id=row["window_id"], job_dir=self.job_dir,
            proposal_sha=proposal["proposal_sha256"], owner_token="owner-test")
        result = run_chirp_recovery_pass(
            ledger=self.ledger, job_id=self.job_id, job_dir=self.job_dir,
            gateway=self.gateway, cost_accountant=lambda _: 2,
            max_operations=8, acoustic_inspector=passing_acoustic_inspector)
        self.assertEqual(result["action"], "CHIRP_COMPLETE")
        self.assertEqual(result["provider_submissions"], 0)


if __name__ == "__main__":
    unittest.main()
