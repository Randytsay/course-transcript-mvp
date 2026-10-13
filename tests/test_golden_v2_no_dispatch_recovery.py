"""Fail-closed tests for aged request-count no-dispatch recovery evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.gemini_primary_audio import _paid_window_slot_digest
from app.pipeline.golden_v2_no_dispatch_recovery import (
    _archive_attempt, _monitoring_window, _verify_metric_window,
    reconcile_primary_no_dispatch,
)


class NoDispatchRecoveryTests(unittest.TestCase):
    def evidence(self):
        start, end = _monitoring_window(
            "2026-10-13T16:35:05.405693+00:00",
            "2026-10-13T16:48:36.397735+00:00",
        )
        point = {"interval": {"endTime": "2026-10-13T16:48:15Z"},
                 "value": {"int64Value": "1"}}
        return {
            "schema": "golden-v2-no-dispatch-evidence-v1",
            "api_project": "test-project",
            "metric_type": "serviceruntime.googleapis.com/api/request_count",
            "window_start": start, "window_end": end,
            "visibility_delay_seconds": 1800,
            "captured_at": "2026-10-13T17:22:00Z",
            "request_count_queries": [
                {"service": "aiplatform.googleapis.com",
                 "filter": 'metric.type="serviceruntime.googleapis.com/api/request_count" AND resource.type="consumed_api" AND resource.labels.service="aiplatform.googleapis.com"',
                 "start": start, "end": end, "pages": [{"http_status": 200,
                     "body": {"timeSeries": [{"resource": {"type": "consumed_api",
                         "labels": {"service": "aiplatform.googleapis.com",
                                    "project_id": "test-project",
                                    "method": "google.cloud.aiplatform.v1beta1.ModelGardenService.GetPublisherModel"}},
                         "points": [point]}]}}]},
                {"service": "generativelanguage.googleapis.com",
                 "filter": 'metric.type="serviceruntime.googleapis.com/api/request_count" AND resource.type="consumed_api" AND resource.labels.service="generativelanguage.googleapis.com"',
                 "start": start, "end": end, "pages": [{"http_status": 200,
                     # Cloud Monitoring omits empty repeated fields in JSON.
                     "body": {"unit": "1"}}]},
            ],
        }

    def test_complete_positive_metric_window_accepts_only_non_generation_methods(self):
        evidence = self.evidence()
        _verify_metric_window(evidence, project="test-project",
                              start=evidence["window_start"], end=evidence["window_end"],
                              captured_at=evidence["captured_at"])

    def test_generation_method_in_window_is_a_hard_stop(self):
        evidence = self.evidence()
        series = evidence["request_count_queries"][0]["pages"][0]["body"]["timeSeries"][0]
        generation = json.loads(json.dumps(series))
        generation["resource"]["labels"]["method"] = "google.cloud.aiplatform.v1.PredictionService.GenerateContent"
        evidence["request_count_queries"][0]["pages"][0]["body"]["timeSeries"].append(generation)
        with self.assertRaises(V2Conflict):
            _verify_metric_window(evidence, project="test-project",
                                  start=evidence["window_start"], end=evidence["window_end"],
                                  captured_at=evidence["captured_at"])

    def test_present_time_series_with_invalid_shape_is_rejected(self):
        evidence = self.evidence()
        evidence["request_count_queries"][1]["pages"][0]["body"]["timeSeries"] = {}
        with self.assertRaises(V2Conflict):
            _verify_metric_window(evidence, project="test-project",
                                  start=evidence["window_start"], end=evidence["window_end"],
                                  captured_at=evidence["captured_at"])

    def test_stale_or_incomplete_metrics_cannot_authorize_retry(self):
        evidence = self.evidence()
        evidence["captured_at"] = "2026-10-13T17:20:59Z"
        with self.assertRaises(V2Conflict):
            _verify_metric_window(evidence, project="test-project",
                                  start=evidence["window_start"], end=evidence["window_end"],
                                  captured_at=evidence["captured_at"])
        evidence = self.evidence()
        evidence["request_count_queries"].pop()
        with self.assertRaises(V2Conflict):
            _verify_metric_window(evidence, project="test-project",
                                  start=evidence["window_start"], end=evidence["window_end"],
                                  captured_at=evidence["captured_at"])

    def test_unknown_raw_attempt_is_preserved_byte_for_byte_before_retry(self):
        with TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            primary = job_dir / "gemini-primary"
            primary.mkdir()
            identity = {"model": "gemini-3.8-flash", "source_sha256": "a" * 64}
            raw = json.dumps({"identity": identity, "status": "OUTCOME_UNKNOWN"},
                             sort_keys=True).encode()
            path = primary / "slot.json"
            path.write_bytes(raw)
            identity_sha = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            evidence = {"source_sha256": "a" * 64,
                        "prior_attempt": {"relative_path": "gemini-primary/slot.json",
                                          "sha256": hashlib.sha256(raw).hexdigest(),
                                          "identity_sha256": identity_sha}}
            archived = _archive_attempt(job_dir, evidence, "e" * 64)
            self.assertFalse(path.exists())
            self.assertEqual(archived.read_bytes(), raw)
            self.assertTrue(archived.is_relative_to(primary / "reconciled"))

    def test_recovery_resumes_after_archive_before_ledger_commit(self):
        class Response:
            status_code = 200

            def __init__(self, body):
                self.body = body

            def json(self):
                return self.body

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, url, *, params, timeout):
                self.calls += 1
                service = params["filter"].rsplit('"', 2)[1]
                if service == "aiplatform.googleapis.com":
                    body = {"timeSeries": [{
                        "resource": {"type": "consumed_api", "labels": {
                            "service": service, "project_id": "test-project",
                            "method": "google.cloud.aiplatform.v1beta1.ModelGardenService.GetPublisherModel",
                        }},
                        "points": [{"interval": {"endTime": params["interval.startTime"]},
                                    "value": {"int64Value": "1"}}],
                    }]}
                else:
                    body = {"timeSeries": []}
                return Response(body)

        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "v2"
            ledger = GoldenV2Ledger(root / "ledger.sqlite")
            source_sha = "a" * 64
            job = ledger.create(
                source_sha=source_sha, mode="FLASH_CHIRP",
                estimated_stage_cents={"gemini_primary": 30, "chirp_independent": 20,
                    "compare": 0, "third_optional": 0, "adjudication": 0, "golden_qa": 0},
            )
            job_id = job
            job_dir = root / "jobs" / job_id
            primary = job_dir / "gemini-primary"
            golden = job_dir / "golden-v2"
            primary.mkdir(parents=True)
            golden.mkdir()
            ledger.approve(job, revision=0, approved_cents=50,
                           actor="owner", reference="test-scope")
            claim = ledger.claim_next(job, worker_id="test")
            ambiguity = {"schema": "golden-v2-ambiguous-primary-v1", "job_id": job,
                         "source_sha256": source_sha, "completed_records": [],
                         "unresolved_window_index": 0}
            ambiguity_raw = json.dumps(ambiguity, sort_keys=True).encode()
            (golden / "primary-ambiguous.json").write_bytes(ambiguity_raw)
            ambiguous_sha = hashlib.sha256(ambiguity_raw).hexdigest()
            ledger.mark_outcome_unknown(claim, evidence_sha=ambiguous_sha)
            status = ledger.status(job)
            created_at = status["created_at"]
            unknown_at = next(x["updated_at"] for x in status["stages"]
                              if x["name"] == "gemini_primary")
            _, end = _monitoring_window(created_at, unknown_at)
            fake_now = datetime.fromisoformat(end.replace("Z", "+00:00")) + timedelta(seconds=1801)

            identity = {
                "model": "gemini-3.8-flash", "prompt_version": "golden-v2-primary-audio-v1",
                "source_sha256": source_sha,
                "window": {"index": 0, "start_seconds": 0, "end_seconds": 90,
                           "gcs_uri": "gs://private-test/window.flac", "mime_type": "audio/flac"},
                "context_sha256": "b" * 64,
            }
            audit_path = primary / f"{_paid_window_slot_digest(identity)}.json"
            audit_raw = json.dumps({"identity": identity, "status": "OUTCOME_UNKNOWN",
                                    "error_type": "RuntimeError"}, sort_keys=True).encode()
            audit_path.write_bytes(audit_raw)

            real_datetime = datetime

            class FrozenDateTime(real_datetime):
                @classmethod
                def now(cls, tz=None):
                    return fake_now if tz is not None else fake_now.replace(tzinfo=None)

            session = Session()
            with patch("app.pipeline.golden_v2_no_dispatch_recovery.datetime", FrozenDateTime):
                def interrupt_after_archive(*args, **kwargs):
                    kwargs["attempt_archiver"]()
                    raise V2Conflict("simulated interruption after archive")

                with patch.object(ledger, "reconcile_unsubmitted_provider_call",
                                  side_effect=interrupt_after_archive):
                    with self.assertRaises(V2Conflict):
                        reconcile_primary_no_dispatch(
                            ledger=ledger, job_id=job, artifact_root=root,
                            project="test-project", session=session,
                        )
                self.assertFalse(audit_path.exists())
                self.assertEqual(ledger.status(job)["status"], "NEEDS_RECONCILIATION")
                self.assertEqual(session.calls, 2)
                result = reconcile_primary_no_dispatch(
                    ledger=ledger, job_id=job, artifact_root=root,
                    project="test-project", session=session,
                )
            self.assertEqual(session.calls, 4)
            self.assertEqual(result["status"], "REOPENED_PENDING")
            self.assertIsNotNone(result["revalidation_evidence_sha256"])
            self.assertTrue((job_dir / result["revalidation_artifact"]).is_file())
            self.assertEqual(ledger.status(job)["stages"][0]["state"], "PENDING")
            archived = job_dir / result["archived_attempt"]
            self.assertEqual(archived.read_bytes(), audit_raw)
            self.assertEqual(ledger.claim_next(job, worker_id="retry")["stage"], "gemini_primary")


if __name__ == "__main__":
    unittest.main()
