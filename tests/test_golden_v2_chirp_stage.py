"""Real local FLAC plus fake cloud, Speech and restart: no paid API involved."""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_chirp_stage import (
    finish_approved_chirp, plan_approved_chirp, recover_chirp_window,
    submit_planned_chirp,
)
from app.pipeline.golden_v2_chirp_repair import (
    approve_and_stage_standard_repairs, propose_standard_repair_plan,
    resume_committed_standard_repair_uploads, submit_approved_standard_repairs,
    upload_committed_standard_repair_clips,
)
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_source_lineage import bind_source_lineage
from app.providers.golden_v2_chirp_gateway import ChirpV2Gateway
from tests.test_golden_v2_primary_stage import FakeCloud


class FakeResult:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self.data = data

    def download_as_bytes(self):
        return self.data


class SpeechFake:
    def __init__(self):
        self.calls = []
        self.operations = {}
        self.fail = False
        self.transport = SimpleNamespace(operations_client=SimpleNamespace(
            get_operation=self.get_operation))

    def batch_recognize(self, *, request, retry, timeout):
        assert retry is None  # POST retries would double bill
        assert timeout <= 60
        self.calls.append(request)
        if self.fail:
            raise TimeoutError("server may have accepted POST")
        name = f"projects/fake-project/locations/us/operations/mock-{len(self.calls)}"
        self.operations[name] = SimpleNamespace(done=False, error=None)
        return SimpleNamespace(operation=SimpleNamespace(name=name))

    def get_operation(self, name):
        if name not in self.operations:
            raise AssertionError("unknown persisted operation")
        return self.operations[name]


class ResultCloud(FakeCloud):
    def __init__(self):
        super().__init__()
        self.results = {}

    def list_blobs(self, *, prefix):
        return [v for k, v in self.results.items() if k.startswith(prefix)]


def speech_words(*words):
    return json.dumps({"results": [{"alternatives": [{"words": [
        {"word": text, "startOffset": f"{start:.3f}s", "endOffset": f"{end:.3f}s"}
        for text, start, end in words
    ]}]}]}, ensure_ascii=False).encode()


def passing_acoustic_inspector(*, job_dir, merged):
    encoded = json.dumps(merged, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return {"schema": "golden-v2-acoustic-qa-v1", "status": "PASS",
            "source_sha256": merged["source_sha"],
            "merged_sha256": hashlib.sha256(encoded).hexdigest(),
            "gaps_examined": [], "repair_plan": {"intervals": []},
            "local_only": True, "provider_calls_started": False}


class CompletedRepairSuppressionTests(unittest.TestCase):
    def test_repaired_speech_gap_stays_review_without_duplicate_proposal(self):
        from app.pipeline.golden_v2_chirp_stage import suppress_completed_repair_proposals
        gap = {"start_ms": 7934160, "end_ms": 7950960,
               "target_intervals": [{"start_ms": 7934160, "end_ms": 7950960}]}
        report = {"status": "REVIEW", "repair_plan": {
            "status": "PROPOSED_REQUIRES_APPROVAL",
            "intervals": [gap], "total_duration_ms": 16800,
            "manual_review_required": False}}
        repaired = [{"purpose": "REPAIR", "status": "DONE",
                     "target_intervals_json": json.dumps([
                         {"start_ms": 7855700, "end_ms": 8013460}])}]
        outcome = suppress_completed_repair_proposals(report, repaired)
        self.assertEqual(outcome["status"], "REVIEW")
        self.assertEqual(outcome["repair_plan"]["intervals"], [])
        self.assertEqual(outcome["repair_plan"]["status"], "NONE")
        self.assertTrue(outcome["repair_plan"]["manual_review_required"])
        self.assertEqual(len(outcome["repair_plan"]
                             ["completed_repair_overlap_review_required"]), 1)
        self.assertEqual(report["repair_plan"]["intervals"], [gap])

    def test_new_disjoint_gap_still_repairable(self):
        from app.pipeline.golden_v2_chirp_stage import suppress_completed_repair_proposals
        first = {"start_ms": 100, "end_ms": 300,
                 "target_intervals": [{"start_ms": 100, "end_ms": 300}]}
        second = {"start_ms": 500, "end_ms": 700,
                  "target_intervals": [{"start_ms": 500, "end_ms": 700}]}
        report = {"status": "REVIEW", "repair_plan": {
            "intervals": [first, second], "status": "PROPOSED_REQUIRES_APPROVAL",
            "total_duration_ms": 400}}
        repairs = [{"purpose": "REPAIR", "status": "DONE",
                    "target_intervals_json": json.dumps([
                        {"start_ms": 50, "end_ms": 350}])}]
        outcome = suppress_completed_repair_proposals(report, repairs)
        self.assertEqual(outcome["repair_plan"]["intervals"], [second])
        self.assertEqual(outcome["repair_plan"]["total_duration_ms"], 200)
        self.assertEqual(outcome["status"], "REVIEW")

    def test_incomplete_attempt_does_not_suppress_repair(self):
        from app.pipeline.golden_v2_chirp_stage import suppress_completed_repair_proposals
        gap = {"start_ms": 100, "end_ms": 300,
               "target_intervals": [{"start_ms": 100, "end_ms": 300}]}
        report = {"status": "REVIEW", "repair_plan": {"intervals": [gap]}}
        pending = [{"purpose": "REPAIR", "status": "SUBMITTED",
                    "target_intervals_json": json.dumps([
                        {"start_ms": 50, "end_ms": 350}])}]
        self.assertIs(suppress_completed_repair_proposals(report, pending), report)


class ChirpStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job_dir = self.root / "job"
        self.job_dir.mkdir()
        self.audio = self.job_dir / "normalized.flac"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error",
                        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=16000",
                        "-t", "5", "-ac", "1", "-c:a", "flac", "-y", str(self.audio)],
                       check=True, capture_output=True, timeout=20)
        original = self.root / "original.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(self.audio),
                        "-c:a", "pcm_s16le", "-y", str(original)],
                       check=True, capture_output=True, timeout=20)
        self.sha = file_sha256(self.audio)
        bind_source_lineage(
            original_media=original, normalized_audio=self.audio,
            drive_observation={"provider": "google_drive", "file_id": "test-only", "version": "1",
                               "downloaded_sha256": file_sha256(original),
                               "size_bytes": original.stat().st_size},
            expected_normalized_sha256=self.sha,
            output_path=self.job_dir / "golden-v2" / "source-lineage.json")
        self.ledger_path = self.root / "ledger.db"
        self.ledger = GoldenV2Ledger(self.ledger_path)
        self.job_id = self.ledger.create(
            source_sha=self.sha, mode="FLASH_CHIRP",
            estimated_stage_cents=dict(gemini_primary=1, chirp_independent=10,
                                       compare=0, third_optional=0, adjudication=0,
                                       golden_qa=0))
        self.ledger.approve(self.job_id, revision=0, approved_cents=11,
                            reference="FAKE", actor="test")
        first = self.ledger.claim_next(self.job_id, worker_id="mock")
        self.ledger.complete(first, evidence_sha="a" * 64, actual_cents=1)
        self.claim = self.ledger.claim_next(self.job_id, worker_id="mock")
        self.cloud = ResultCloud()
        self.speech = SpeechFake()
        self.gateway = ChirpV2Gateway(
            speech_client=self.speech, storage_client=self.cloud,
            project="fake-project", request_factory=lambda row: dict(row))

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, **options):
        arguments = dict(
            ledger=self.ledger, claim=self.claim, normalized_audio=self.audio,
            job_dir=self.job_dir, storage_client=self.cloud, bucket_name="private-test-bucket",
            object_prefix="v2/staging", estimate_window_cents=lambda _: 2,
            max_window_seconds=3, overlap_seconds=0.5)
        arguments.update(options)
        return plan_approved_chirp(**arguments)

    def test_verified_numeric_project_operation_alias(self):
        self.plan()
        row = self.ledger.begin_chirp_submit(self.claim, 'chunk-0000')
        numeric = 'projects/608343797618/locations/us/operations/real-shaped-test'
        self.speech.batch_recognize = lambda **_: SimpleNamespace(operation=SimpleNamespace(name=numeric))
        gateway = ChirpV2Gateway(speech_client=self.speech, storage_client=self.cloud,
            project='fake-project', project_number='608343797618', request_factory=lambda r:dict(r))
        self.assertEqual(gateway.submit(row), numeric)
        self.speech.operations[numeric] = SimpleNamespace(done=False, error=None)
        self.ledger.record_chirp_submission(self.claim, 'chunk-0000', numeric)
        saved = self.ledger.chirp_windows(self.job_id)[0]
        self.assertEqual(gateway.read_result(saved), {'status':'PENDING'})
        with self.assertRaises(V2Conflict):
            self.gateway.read_result(saved)
        with self.assertRaises(ValueError):
            ChirpV2Gateway(speech_client=self.speech, storage_client=self.cloud,
                          project='fake-project', project_number='unverified-other-project')

    def test_raw_operation_is_recorded_before_identity_rejection(self):
        self.plan()
        row = self.ledger.begin_chirp_submit(self.claim, 'chunk-0000')
        operation = SimpleNamespace(name='projects/unexpected/locations/us/operations/test')
        self.speech.batch_recognize = lambda **_: SimpleNamespace(operation=operation)
        receipts = []
        gateway = ChirpV2Gateway(speech_client=self.speech, storage_client=self.cloud,
            project='fake-project', request_factory=lambda r:dict(r),
            operation_recorder=lambda r,op:receipts.append((r['request_id'],op)))
        with self.assertRaises(V2Conflict):
            gateway.submit(row)
        self.assertEqual(receipts, [(row['request_id'], operation)])
        self.assertEqual(self.ledger.chirp_windows(self.job_id)[0]['status'], 'SUBMITTING')

    def test_reconcile_exact_saved_request_without_second_submission(self):
        from google.longrunning import operations_pb2
        from google.cloud.speech_v2.types import cloud_speech
        from app.pipeline.golden_v2_primary_rescue import reconcile_exact_chirp_operation
        self.plan()
        row = self.ledger.begin_chirp_submit(self.claim, 'chunk-0000')
        metadata = cloud_speech.OperationMetadata.pb(cloud_speech.OperationMetadata(
            batch_recognize_request=cloud_speech.BatchRecognizeRequest(
                config=cloud_speech.RecognitionConfig(model='chirp_3'),
                files=[cloud_speech.BatchRecognizeFileMetadata(uri=row['gcs_uri'])],
                recognition_output_config=cloud_speech.RecognitionOutputConfig(
                    gcs_output_config=cloud_speech.GcsOutputConfig(uri=row['output_uri'])))))
        operation = operations_pb2.Operation(name='projects/123/locations/us/operations/v2-test')
        operation.metadata.Pack(metadata)
        args = dict(ledger=self.ledger, job_id=self.job_id,
            revision=self.ledger.status(self.job_id)['revision'], window_id='chunk-0000',
            operation=operation, project_number='123', reference='exact request GET')
        reconcile_exact_chirp_operation(**args)
        self.assertEqual(self.ledger.chirp_windows(self.job_id)[0]['operation_name'], operation.name)
        self.assertEqual(self.speech.calls, [])
        with self.assertRaises(V2Conflict):
            reconcile_exact_chirp_operation(**args)

    def test_provider_omitted_zero_start_is_only_valid_for_first_word(self):
        from app.pipeline.golden_v2_chirp_stage import _parse_words
        payload = {'results':[{'alternatives':[{'words':[
            {'word':'我', 'endOffset':'0.920s'},
            {'word':'常', 'startOffset':'1.120s', 'endOffset':'1.360s'}]}]}]}
        window = dict(start_ms=900000, end_ms=1800000)
        words = _parse_words(json.dumps(payload).encode(), window)
        self.assertEqual(words[0]['start_ms'], 900000)
        del payload['results'][0]['alternatives'][0]['words'][1]['startOffset']
        with self.assertRaises(V2Conflict):
            _parse_words(json.dumps(payload).encode(), window)

    def test_missing_default_end_is_coalesced_only_with_same_start_successor(self):
        from app.pipeline.golden_v2_chirp_stage import _parse_words
        payload = {"results": [{"alternatives": [{"words": [
            {"word": "越", "startOffset": "1.000s", "endOffset": "1.080s"},
            {"word": "遠", "startOffset": "1.080s"},
            {"word": "。", "startOffset": "1.080s", "endOffset": "1.160s"},
        ]}]}]}
        words = _parse_words(json.dumps(payload, ensure_ascii=False).encode(),
                             {"start_ms": 0, "end_ms": 3000})
        self.assertEqual(words, [
            {"word": "越", "start_ms": 1000, "end_ms": 1080},
            {"word": "遠。", "start_ms": 1080, "end_ms": 1160,
             "timing_resolution": "COALESCED_ZERO_DURATION_SEQUENCE"},
        ])

        payload["results"][0]["alternatives"][0]["words"][2]["startOffset"] = "1.120s"
        with self.assertRaisesRegex(V2Conflict, "same-time successor"):
            _parse_words(json.dumps(payload, ensure_ascii=False).encode(),
                         {"start_ms": 0, "end_ms": 3000})

    def test_out_of_order_provider_words_are_sorted_but_overlaps_still_fail(self):
        from app.pipeline.golden_v2_chirp_stage import _parse_words
        payload = {"results": [{"alternatives": [{"words": [
            {"word": "後", "startOffset": "2s", "endOffset": "2.2s"},
            {"word": "先", "startOffset": "1s", "endOffset": "1.1s"},
        ]}]}]}
        parsed = _parse_words(json.dumps(payload, ensure_ascii=False).encode(),
                              {"start_ms": 0, "end_ms": 3000})
        self.assertEqual([item["word"] for item in parsed], ["先", "後"])
        payload["results"][0]["alternatives"][0]["words"][1]["startOffset"] = "2.1s"
        payload["results"][0]["alternatives"][0]["words"][1]["endOffset"] = "2.3s"
        with self.assertRaisesRegex(V2Conflict, "overlap"):
            _parse_words(json.dumps(payload, ensure_ascii=False).encode(),
                         {"start_ms": 0, "end_ms": 3000})

    def _complete_outputs(self, rows):
        for i, row in enumerate(rows):
            self.speech.operations[row["operation_name"]].done = True
            prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
            self.cloud.results[prefix + "output.json"] = FakeResult(
                prefix + "output.json", speech_words((f"第{i+1}句", 0.2, 0.65)))

    def test_restart_recovers_exact_operations_and_timing(self):
        self.assertEqual(len(self.plan()), 2)
        self.assertEqual(len(self.cloud.objects), 2)
        rows = submit_planned_chirp(ledger=self.ledger, claim=self.claim,
                                    gateway=self.gateway)
        self.assertEqual(len(self.speech.calls), 2)
        self.assertTrue(all(row["status"] == "SUBMITTED" for row in rows))
        self._complete_outputs(rows)
        self.ledger = GoldenV2Ledger(self.ledger_path)  # fresh process
        for row in rows:
            self.assertEqual(recover_chirp_window(
                ledger=self.ledger, job_id=self.job_id, window_id=row["window_id"],
                job_dir=self.job_dir, gateway=self.gateway,
                cost_accountant=lambda _: 2), "DONE")
        result = finish_approved_chirp(ledger=self.ledger, job_id=self.job_id,
                                      job_dir=self.job_dir,
                                      acoustic_inspector=passing_acoustic_inspector)
        self.assertEqual([w["word"] for w in result["words"]], ["第1句", "第2句"])
        self.assertEqual(result["timing_authority"], "chirp_3")
        self.assertFalse(result["golden_pass"])
        self.assertEqual(result["acoustic_gate"], "PASS")
        self.assertEqual(self.ledger.status(self.job_id)["spent_cents"], 5)
        self.assertEqual(self.ledger.claim_next(self.job_id, worker_id="next")["stage"], "compare")
        self.assertEqual(len(self.speech.calls), 2)

    def test_ambiguous_post_crash_blocks_automatic_replay(self):
        self.plan(max_window_seconds=10)
        self.speech.fail = True
        with self.assertRaises(TimeoutError):
            submit_planned_chirp(ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        restarted = GoldenV2Ledger(self.ledger_path)
        with self.assertRaisesRegex(V2Conflict, "unresolved Chirp"):
            submit_planned_chirp(ledger=restarted, claim=self.claim, gateway=self.gateway)
        state = restarted.status(self.job_id)
        with self.assertRaisesRegex(V2Conflict, "ambiguous Chirp"):
            restarted.resume_chirp_submissions(
                self.job_id, worker_id="recovery", expected_revision=state["revision"],
                minimum_age_seconds=0,
            )
        self.assertEqual(len(self.speech.calls), 1)
        self.assertEqual(restarted.chirp_windows(self.job_id)[0]["status"], "SUBMITTING")

    def test_restart_resumes_only_unsubmitted_windows_with_rotated_claim(self):
        planned = self.plan()
        first = planned[0]
        intent = self.ledger.begin_chirp_submit(self.claim, first["window_id"])
        operation_name = self.gateway.submit(intent)
        self.ledger.record_chirp_submission(self.claim, first["window_id"], operation_name)

        restarted = GoldenV2Ledger(self.ledger_path)
        state = restarted.status(self.job_id)
        resumed_claim = restarted.resume_chirp_submissions(
            self.job_id, worker_id="recovery", expected_revision=state["revision"],
            minimum_age_seconds=0,
        )
        with self.assertRaisesRegex(V2Conflict, "unrecognized/stale"):
            submit_planned_chirp(ledger=restarted, claim=self.claim, gateway=self.gateway)
        rows = submit_planned_chirp(
            ledger=restarted, claim=resumed_claim, gateway=self.gateway)
        self.assertEqual([row["status"] for row in rows], ["SUBMITTED", "SUBMITTED"])
        self.assertEqual(rows[0]["operation_name"], operation_name)
        self.assertEqual(len(self.speech.calls), 2)

    def test_restart_resume_requires_fresh_revision_and_recovery_grace(self):
        self.plan(max_window_seconds=10)
        stale = self.ledger.status(self.job_id)
        with self.assertRaisesRegex(V2Conflict, "recovery grace"):
            self.ledger.resume_chirp_submissions(
                self.job_id, worker_id="recovery", expected_revision=stale["revision"],
                minimum_age_seconds=300,
            )
        with self.assertRaisesRegex(V2Conflict, "revision, job or stage"):
            self.ledger.resume_chirp_submissions(
                self.job_id, worker_id="recovery", expected_revision=stale["revision"] + 1,
                minimum_age_seconds=0,
            )
        self.assertEqual(len(self.speech.calls), 0)

    def test_standard_repair_is_plan_bound_budgeted_and_uses_standard_gateway(self):
        self.plan(max_window_seconds=10)
        base = submit_planned_chirp(
            ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        self._complete_outputs(base)
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=base[0]["window_id"],
            job_dir=self.job_dir, gateway=self.gateway,
            cost_accountant=lambda _: 2), "DONE")

        clip_sha = "a" * 64
        assessment_sha = "b" * 64
        repair = {
            "window_id": "repair-r01-00", "source_sha": self.sha,
            "clip_sha": clip_sha,
            "gcs_uri": (f"gs://private-test-bucket/v2/staging/{self.job_id}/"
                        f"{self.sha}/chirp-repair-inputs/{clip_sha}.flac"),
            "start_ms": 0, "end_ms": 3000,
            "target_intervals": [{"start_ms": 1000, "end_ms": 2000}],
            "output_uri": (f"gs://private-test-bucket/v2/staging/{self.job_id}/"
                           f"{self.sha}/chirp-repair-outputs/repair-r01-00/"),
            "estimated_cents": 1, "assessment_sha": assessment_sha,
            "strategy": "STANDARD_BATCH",
        }
        state = self.ledger.status(self.job_id)
        approval = self.ledger.approve_chirp_repair_plan(
            self.job_id, expected_revision=state["revision"],
            assessment_sha=assessment_sha, windows=[repair], approved_cents=1,
            reference="owner-approved-test-plan", actor="unit-test-owner",
        )
        changed = {**repair, "output_uri": repair["output_uri"].replace(
            "repair-r01-00/", "different-operation/")}
        with self.assertRaisesRegex(V2Conflict, "differ from owner-approved"):
            self.ledger.plan_chirp_repair_windows(
                self.claim, plan_sha=approval["plan_sha"], windows=[changed])
        self.ledger.plan_chirp_repair_windows(
            self.claim, plan_sha=approval["plan_sha"], windows=[repair])
        planned = [row for row in self.ledger.chirp_windows(self.job_id)
                   if row.get("purpose") == "REPAIR"]
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0]["assessment_sha"], assessment_sha)
        self.assertEqual(json.loads(planned[0]["target_intervals_json"]),
                         [{"start_ms": 1000, "end_ms": 2000}])

        calls_before = len(self.speech.calls)
        with self.assertRaisesRegex(V2Conflict, "Standard repair requires"):
            submit_planned_chirp(
                ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        self.assertEqual(len(self.speech.calls), calls_before)
        self.gateway.dynamic_batching = False
        submitted = submit_planned_chirp(
            ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        self.assertEqual(submitted[-1]["purpose"], "REPAIR")
        self.assertEqual(submitted[-1]["status"], "SUBMITTED")
        self.assertEqual(len(self.speech.calls), calls_before + 1)

    def test_standard_repair_approval_fails_before_base_evidence_and_above_budget(self):
        self.plan(max_window_seconds=10)
        repair = {
            "window_id": "repair-r01-00", "source_sha": self.sha,
            "clip_sha": "a" * 64,
            "gcs_uri": "gs://private-test-bucket/v2/in.flac",
            "start_ms": 0, "end_ms": 3000,
            "target_intervals": [{"start_ms": 1000, "end_ms": 2000}],
            "output_uri": "gs://private-test-bucket/v2/out/",
            "estimated_cents": 1, "assessment_sha": "b" * 64,
            "strategy": "STANDARD_BATCH",
        }
        state = self.ledger.status(self.job_id)
        with self.assertRaisesRegex(V2Conflict, "all prior Chirp operations complete"):
            self.ledger.approve_chirp_repair_plan(
                self.job_id, expected_revision=state["revision"],
                assessment_sha="b" * 64, windows=[repair], approved_cents=1,
                reference="owner-test", actor="unit-test-owner")
        self.assertEqual(len(self.speech.calls), 0)

    def test_end_to_end_standard_repair_preserves_base_and_gates_compare(self):
        base = self.plan(max_window_seconds=10, estimate_window_cents=lambda _: 8)
        submitted = submit_planned_chirp(
            ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        self.speech.operations[submitted[0]["operation_name"]].done = True
        input_prefix = submitted[0]["output_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.results[input_prefix + "output.json"] = FakeResult(
            input_prefix + "output.json", speech_words(("舊辨識", 1.2, 1.5)))
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=submitted[0]["window_id"],
            job_dir=self.job_dir, gateway=self.gateway,
            cost_accountant=lambda _: 2), "DONE")

        def review_one_gap(*, job_dir, merged):
            digest = hashlib.sha256(json.dumps(
                merged, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            return {
                "schema": "golden-v2-acoustic-qa-v1", "status": "REVIEW",
                "source_sha256": self.sha, "merged_sha256": digest,
                "gaps_examined": [{"start_ms": 1000, "end_ms": 2000,
                                   "disposition": "SPEECH_CONFIRMED"}],
                "repair_plan": {
                    "strategy": "STANDARD_BATCH", "submit_authorized": False,
                    "status": "PROPOSED_REQUIRES_APPROVAL",
                    "intervals": [{
                        "start_ms": 500, "end_ms": 2500,
                        "target_intervals": [{"start_ms": 1000, "end_ms": 2000,
                                              "gap_index": 0}],
                    }],
                    "total_duration_ms": 2000, "truncated": False,
                    "manual_review_required": False,
                },
                "local_only": True, "provider_calls_started": False,
            }

        review = finish_approved_chirp(
            ledger=self.ledger, job_id=self.job_id, job_dir=self.job_dir,
            acoustic_inspector=review_one_gap)
        self.assertEqual(review["acoustic_gate"], "REVIEW")
        self.assertFalse((self.job_dir / "golden-v2" / "chirp-merged.json").exists())
        with self.assertRaisesRegex(V2Conflict, "unresolved: RUNNING"):
            self.ledger.claim_next(self.job_id, worker_id="compare-too-early")

        proposal = propose_standard_repair_plan(
            ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
            normalized_audio=self.audio,
            acoustic_report_path=Path(review["acoustic_qa_path"]),
            bucket_name="private-test-bucket", object_prefix="v2/staging",
            estimate_window_cents=lambda _: 3,
        )
        staged = approve_and_stage_standard_repairs(
            ledger=self.ledger, claim=self.claim, proposal=proposal,
            approved_cents=3, approval_reference="exact-gap-test-approval",
            approving_actor="unit-test-owner", bucket_name="private-test-bucket",
            object_prefix="v2/staging", storage_client=self.cloud,
        )
        self.assertEqual(staged["status"], "STANDARD_REPAIR_STAGED")
        self.assertEqual(staged["provider_submissions"], 0)
        repair_row = next(row for row in self.ledger.chirp_windows(self.job_id)
                          if row.get("purpose") == "REPAIR")
        self.assertEqual(repair_row["status"], "PLANNED")
        upload_count = sum(blob.uploads for blob in self.cloud.objects.values())
        retry = upload_committed_standard_repair_clips(
            ledger=self.ledger, claim=self.claim, proposal=proposal,
            bucket_name="private-test-bucket", object_prefix="v2/staging",
            storage_client=self.cloud,
        )
        self.assertEqual(retry["uploaded_windows"], 1)
        self.assertEqual(sum(blob.uploads for blob in self.cloud.objects.values()),
                         upload_count)

        # Simulate an upload that was absent after process restart. The ledger
        # retains the exact plan and rotates its claim before safe re-upload.
        object_key = repair_row["gcs_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.objects.pop(object_key)
        self.ledger = GoldenV2Ledger(self.ledger_path)
        state = self.ledger.status(self.job_id)
        resumed_claim = self.ledger.resume_chirp_submissions(
            self.job_id, worker_id="repair-upload-recovery",
            expected_revision=state["revision"], minimum_age_seconds=0,
        )
        with self.assertRaisesRegex(V2Conflict, "unrecognized/stale"):
            resume_committed_standard_repair_uploads(
                ledger=self.ledger, claim=self.claim, job_dir=self.job_dir,
                normalized_audio=self.audio, bucket_name="private-test-bucket",
                object_prefix="v2/staging", storage_client=self.cloud,
            )
        recovered_upload = resume_committed_standard_repair_uploads(
            ledger=self.ledger, claim=resumed_claim, job_dir=self.job_dir,
            normalized_audio=self.audio, bucket_name="private-test-bucket",
            object_prefix="v2/staging", storage_client=self.cloud,
        )
        self.assertEqual(recovered_upload["uploaded_windows"], 1)

        standard_gateway = ChirpV2Gateway(
            speech_client=self.speech, storage_client=self.cloud,
            project="fake-project", dynamic_batching=False,
            request_factory=lambda row: dict(row),
        )
        repair_submitted = submit_approved_standard_repairs(
            ledger=self.ledger, claim=resumed_claim, gateway=standard_gateway)
        self.assertEqual(len(repair_submitted), 1)
        self.assertFalse(standard_gateway.dynamic_batching)
        repair = repair_submitted[0]
        self.speech.operations[repair["operation_name"]].done = True
        repair_prefix = repair["output_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.results[repair_prefix + "output.json"] = FakeResult(
            repair_prefix + "output.json", speech_words(("新辨識", 0.7, 1.0)))
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=repair["window_id"],
            job_dir=self.job_dir, gateway=standard_gateway,
            cost_accountant=lambda _: 1), "DONE")
        final = finish_approved_chirp(
            ledger=self.ledger, job_id=self.job_id, job_dir=self.job_dir,
            acoustic_inspector=passing_acoustic_inspector)
        self.assertEqual(final["acoustic_gate"], "PASS")
        self.assertEqual([word["word"] for word in final["words"]], ["新辨識"])
        self.assertTrue((self.job_dir / "golden-v2" / "chirp-merged.json").is_file())
        self.assertEqual(self.ledger.claim_next(self.job_id, worker_id="compare") ["stage"],
                         "compare")

    def test_preflight_cost_and_source_guards_prevent_cloud(self):
        with self.assertRaises(V2Conflict):
            self.plan(estimate_window_cents=lambda _: 10)
        self.assertFalse(self.cloud.objects)
        self.audio.write_bytes(b"changed")
        with self.assertRaises(Exception):
            self.plan()
        self.assertFalse(self.cloud.objects)

    def test_verified_overrun_is_charged_and_freezes_new_work(self):
        self.plan(max_window_seconds=10)
        row = submit_planned_chirp(ledger=self.ledger, claim=self.claim,
                                   gateway=self.gateway)[0]
        self.speech.operations[row["operation_name"]].done = True
        prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.results[prefix + "out.json"] = FakeResult(
            prefix + "out.json", speech_words(("問題", 0.1, 0.5)))
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=row["window_id"],
            job_dir=self.job_dir, gateway=self.gateway,
            cost_accountant=lambda _: 50), "DONE")
        self.assertEqual(self.ledger.chirp_windows(self.job_id)[0]["status"], "DONE")
        status = self.ledger.status(self.job_id)
        self.assertEqual(status["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(status["spent_cents"], 51)
        self.assertEqual(status["stages"][1]["state"], "OVER_BUDGET")
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job_id, worker_id="blocked")

    def test_recover_other_submitted_lro_after_budget_freeze_without_replay(self):
        self.plan()
        rows = submit_planned_chirp(ledger=self.ledger, claim=self.claim,
                                    gateway=self.gateway)
        self._complete_outputs(rows)
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=rows[0]["window_id"],
            job_dir=self.job_dir, gateway=self.gateway,
            cost_accountant=lambda _: 20), "DONE")
        self.assertEqual(self.ledger.status(self.job_id)["spent_cents"], 21)
        self.ledger = GoldenV2Ledger(self.ledger_path)
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=rows[1]["window_id"],
            job_dir=self.job_dir, gateway=self.gateway,
            cost_accountant=lambda _: 3), "DONE")
        self.assertEqual(self.ledger.status(self.job_id)["spent_cents"], 24)
        self.assertEqual([w["status"] for w in self.ledger.chirp_windows(self.job_id)],
                         ["DONE", "DONE"])
        self.assertEqual(len(self.speech.calls), 2)
        with self.assertRaises(V2Conflict):
            submit_planned_chirp(ledger=self.ledger, claim=self.claim, gateway=self.gateway)
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job_id, worker_id="blocked")

    def test_unsafe_word_time_must_not_be_accepted(self):
        self.plan(max_window_seconds=10)
        row = submit_planned_chirp(ledger=self.ledger, claim=self.claim,
                                   gateway=self.gateway)[0]
        self.speech.operations[row["operation_name"]].done = True
        prefix = row["output_uri"].split("private-test-bucket/", 1)[1]
        self.cloud.results[prefix + "out.json"] = FakeResult(
            prefix + "out.json", speech_words(("越界", 10, 11)))
        self.assertEqual(recover_chirp_window(
            ledger=self.ledger, job_id=self.job_id, window_id=row["window_id"],
            job_dir=self.job_dir, gateway=self.gateway,
            cost_accountant=lambda _: 2), "REVIEW_REQUIRED")
        recovered = self.ledger.chirp_windows(self.job_id)[0]
        self.assertEqual(recovered["status"], "REVIEW_REQUIRED")
        self.assertEqual(recovered["actual_cents"], 2)
        evidence = (self.job_dir / "golden-v2" / "chirp-evidence"
                    / row["window_id"] / "provider-raw.json")
        self.assertTrue(evidence.is_file())
        with self.assertRaisesRegex(V2Conflict, "all Chirp operations"):
            finish_approved_chirp(ledger=self.ledger, job_id=self.job_id,
                                  job_dir=self.job_dir)

    def test_completed_speech_lro_per_file_failure_never_recovers(self):
        self.plan(max_window_seconds=10)
        row = submit_planned_chirp(ledger=self.ledger, claim=self.claim,
                                   gateway=self.gateway)[0]
        operation = self.speech.operations[row["operation_name"]]
        operation.done = True
        operation.response = SimpleNamespace(results={
            row["gcs_uri"]: SimpleNamespace(error=SimpleNamespace(code=8, message="quota")),
        })
        with self.assertRaisesRegex(V2Conflict, "per-file provider error"):
            recover_chirp_window(ledger=self.ledger, job_id=self.job_id,
                                 window_id=row["window_id"], job_dir=self.job_dir,
                                 gateway=self.gateway, cost_accountant=lambda _: 2)
        self.assertEqual(self.ledger.chirp_windows(self.job_id)[0]["status"], "SUBMITTED")
        self.assertEqual(len(self.speech.calls), 1)

    def test_completed_speech_lro_wrong_file_identity_blocks_recovery(self):
        self.plan(max_window_seconds=10)
        row = submit_planned_chirp(ledger=self.ledger, claim=self.claim,
                                   gateway=self.gateway)[0]
        operation = self.speech.operations[row["operation_name"]]
        operation.done = True
        operation.response = SimpleNamespace(results={
            "gs://wrong-bucket/other.flac": SimpleNamespace(error=None),
        })
        with self.assertRaisesRegex(V2Conflict, "input URI"):
            recover_chirp_window(ledger=self.ledger, job_id=self.job_id,
                                 window_id=row["window_id"], job_dir=self.job_dir,
                                 gateway=self.gateway, cost_accountant=lambda _: 2)
        self.assertEqual(len(self.speech.calls), 1)


if __name__ == "__main__":
    unittest.main()
