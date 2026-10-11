"""Real local FLAC plus fake cloud, Speech and restart: no paid API involved."""
from __future__ import annotations

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
                                      job_dir=self.job_dir)
        self.assertEqual([w["word"] for w in result["words"]], ["第1句", "第2句"])
        self.assertEqual(result["timing_authority"], "chirp_3")
        self.assertFalse(result["golden_pass"])
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
        self.assertEqual(len(self.speech.calls), 1)
        self.assertEqual(restarted.chirp_windows(self.job_id)[0]["status"], "SUBMITTING")

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
        with self.assertRaisesRegex(V2Conflict, "offsets out of source"):
            recover_chirp_window(
                ledger=self.ledger, job_id=self.job_id, window_id=row["window_id"],
                job_dir=self.job_dir, gateway=self.gateway,
                cost_accountant=lambda _: 2)
        self.assertEqual(self.ledger.chirp_windows(self.job_id)[0]["status"], "SUBMITTED")

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
