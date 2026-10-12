"""Offline end-to-end Gemini audio stage with real ffmpeg, fake GCS/Vertex."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.providers.golden_v2_audio_staging import file_sha256
from app.pipeline.golden_v2_primary_stage import (
    execute_approved_primary_stage, finalize_reviewed_primary_overlaps,
    verify_primary_overlap_artifacts,
)
from app.pipeline.golden_v2_stage_runner import complete_comparison
from scripts.golden_v2_review_overlaps import inspect_pending_overlaps


class FakeBlob:
    def __init__(self):
        self.present = False
        self.metadata = {}
        self.size = None
        self.uploads = 0

    def exists(self, *, client=None):
        return self.present

    def upload_from_filename(self, name, *, content_type, if_generation_match):
        assert if_generation_match == 0
        assert not self.present
        self.size = Path(name).stat().st_size
        self.present = True
        self.uploads += 1

    def reload(self, *, client=None):
        pass


class FakeCloud:
    def __init__(self):
        self.objects = {}

    def bucket(self, name):
        assert name == "private-test-bucket"
        return self

    def blob(self, key):
        return self.objects.setdefault(key, FakeBlob())


class GeminiStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.source = self.dir / "normalized.flac"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
             "-t", "4", "-ac", "1", "-c:a", "flac", "-y", str(self.source)],
            check=True, capture_output=True, timeout=20,
        )
        self.original = self.dir / "original.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(self.source),
             "-c:a", "pcm_s16le", "-y", str(self.original)],
            check=True, capture_output=True, timeout=20,
        )
        self.observation = {
            "provider": "google_drive", "file_id": "mock-drive-id", "version": "v1",
            "size_bytes": self.original.stat().st_size,
            "downloaded_sha256": file_sha256(self.original),
        }
        self.sha = file_sha256(self.source)
        self.ledger = GoldenV2Ledger(self.dir / "jobs.db")
        self.job = self.ledger.create(
            source_sha=self.sha, mode="QUALITY_3WAY",
            estimated_stage_cents={
                "gemini_primary": 5, "chirp_independent": 10,
                "compare": 0, "third_optional": 3,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.ledger.approve(self.job, revision=0, approved_cents=18,
                            actor="mock-owner", reference="mock-no-real-spend")
        self.claim = self.ledger.claim_next(self.job, worker_id="mock-worker")
        self.cloud = FakeCloud()
        self.vertex = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
            return_value=SimpleNamespace(
                text="復次文殊師利男身具七寶",
                candidates=[SimpleNamespace(finish_reason="STOP")],
                usage_metadata={"prompt_token_count": 1},
            ),
        )))

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, **options):
        args = dict(
            ledger=self.ledger, claim=self.claim,
            normalized_audio=self.source,
            original_media=self.original, drive_observation=self.observation,
            job_dir=self.dir / "job",
            storage_client=self.cloud,
            vertex_client=self.vertex,
            bucket_name="private-test-bucket",
            object_prefix="v2/staging",
            cost_accountant=lambda records: len(records),
            cost_estimator=lambda manifest: manifest["local_window_count"],
            part_factory=lambda uri, mime: (uri, mime),
            max_window_seconds=9,
            overlap_seconds=0.5,
        )
        args.update(options)
        return execute_approved_primary_stage(**args)

    def test_single_file_produces_durable_gemini_primary_before_chirp(self):
        report = self.execute()
        self.assertTrue(report["ready_for_chirp"])
        self.assertEqual(report["actual_cents"], 1)
        self.assertEqual(len(report["source_lineage_sha256"]), 64)
        self.assertTrue((self.dir/"job/golden-v2/source-lineage.json").is_file())
        self.assertEqual(self.vertex.models.generate_content.call_count, 1)
        self.assertEqual(self.ledger.status(self.job)["spent_cents"], 1)
        self.assertTrue((self.dir/"job/golden-v2/primary-stage.json").is_file())
        self.assertEqual(
            self.ledger.claim_next(self.job, worker_id="next")["stage"],
            "chirp_independent",
        )

    def test_overlap_is_recorded_for_review_and_billed_cost_not_hidden(self):
        report = self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        self.assertEqual(len(report["windows"]), 2)
        self.assertFalse(report["ready_for_chirp"])
        state = self.ledger.status(self.job)
        self.assertEqual(state["status"], "NEEDS_REVIEW")
        self.assertEqual(state["spent_cents"], 2)
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job, worker_id="next")

    def test_review_inspection_checks_audio_raw_and_existing_immutable_report(self):
        self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        params = dict(database=self.dir / "jobs.db", job_id=self.job,
                      job_dir=self.dir / "job", normalized_audio=self.source)
        before = (self.dir / "jobs.db").read_bytes()
        report = inspect_pending_overlaps(**params)
        self.assertEqual(report["whole_file_qa"], "UNVERIFIED")
        self.assertEqual(len(report["seams"]), 1)
        self.assertEqual(report["seams"][0]["audio_start_ms"], 2000)
        self.assertTrue(report["seams"][0]["left_window_text_tail"])
        self.assertEqual((self.dir / "jobs.db").read_bytes(), before)
        raw = next((self.dir / "job/gemini-primary").glob("*.json"))
        original = raw.read_bytes()
        raw.write_text("{}", encoding="utf-8")
        with self.assertRaises(V2Conflict):
            inspect_pending_overlaps(**params)
        raw.write_bytes(original)
        self.source.write_bytes(b"changed audio")
        with self.assertRaises(V2Conflict):
            inspect_pending_overlaps(**params)
        self.assertEqual(self.vertex.models.generate_content.call_count, 2)

    def test_overlap_evidence_rejects_tampering_even_with_rehashed_report(self):
        self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        path = self.dir / "job/golden-v2/primary-stage.json"
        pristine = json.loads(path.read_text(encoding="utf-8"))
        checks = {
            "wrong model": lambda r: r["window_evidence"][0]["identity"].update(model="fake"),
            "wrong prompt": lambda r: r["window_evidence"][0]["identity"].update(prompt_version="fake"),
            "wrong source": lambda r: r["window_evidence"][0]["identity"].update(source_sha256="a" * 64),
            "wrong staging": lambda r: r["window_evidence"][0]["identity"]["window"].update(gcs_uri="gs://fake/other"),
            "missing raw sha": lambda r: r["window_evidence"][0].pop("raw_audit_sha256"),
            "bad audit container": lambda r: r.update(window_evidence=[None]),
            "bad windows": lambda r: r.update(windows="bad"),
            "non-finite window": lambda r: r["windows"][0].update(start_seconds=float("nan")),
            "forged assembly": lambda r: r["assembled"].update(text="made-up transcript"),
            "omitted seam": lambda r: r["assembled"].update(review_items=[]),
        }
        for name, mutate in checks.items():
            with self.subTest(name=name):
                variant = copy.deepcopy(pristine)
                mutate(variant)
                path.write_text(json.dumps(variant, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(V2Conflict):
                    verify_primary_overlap_artifacts(
                        job_dir=self.dir / "job", job_id=self.job,
                        source_sha=self.sha, original_sha=file_sha256(path),
                    )
        path.write_bytes(b"{bad-json")
        with self.assertRaises(V2Conflict):
            verify_primary_overlap_artifacts(
                job_dir=self.dir / "job", job_id=self.job,
                source_sha=self.sha, original_sha=file_sha256(path),
            )
        self.assertEqual(self.vertex.models.generate_content.call_count, 2)
        self.assertEqual(self.ledger.status(self.job)["status"], "NEEDS_REVIEW")

    def test_lineage_and_raw_response_must_match_rehashed_primary_report(self):
        self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        job_dir = self.dir / "job"
        report_path = job_dir / "golden-v2/primary-stage.json"
        lineage_path = job_dir / "golden-v2/source-lineage.json"
        original_report = report_path.read_bytes()
        original_lineage = lineage_path.read_bytes()
        original = json.loads(original_report)
        raw_path = next((job_dir / "gemini-primary").glob("*.json"))
        original_raw = raw_path.read_bytes()
        for name in ("lineage", "incomplete_raw", "changed_raw_text"):
            with self.subTest(name=name):
                report = copy.deepcopy(original)
                if name == "lineage":
                    lineage = json.loads(original_lineage)
                    lineage["normalized_sha256"] = "b" * 64
                    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")
                    report["source_lineage_sha256"] = file_sha256(lineage_path)
                else:
                    raw = json.loads(original_raw)
                    if name == "incomplete_raw":
                        raw["status"] = "REQUEST_STARTED"
                    else:
                        raw["text"] = "unapproved fabricated extra speech"
                    raw_path.write_text(json.dumps(raw), encoding="utf-8")
                    first = report["window_evidence"][0]
                    first["raw_audit_sha256"] = file_sha256(raw_path)
                report_path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(V2Conflict):
                    verify_primary_overlap_artifacts(
                        job_dir=job_dir, job_id=self.job, source_sha=self.sha,
                        original_sha=file_sha256(report_path),
                    )
                raw_path.write_bytes(original_raw)
                lineage_path.write_bytes(original_lineage)
                report_path.write_bytes(original_report)
        self.assertEqual(self.vertex.models.generate_content.call_count, 2)

    def test_multi_seam_inspection_requires_every_audio_review_decision(self):
        self.execute(max_window_seconds=1.8, overlap_seconds=0.3)
        inspection = inspect_pending_overlaps(
            database=self.dir / "jobs.db", job_id=self.job,
            job_dir=self.dir / "job", normalized_audio=self.source,
        )
        self.assertEqual(len(inspection["seams"]), 2)
        self.assertEqual([s["between_windows"] for s in inspection["seams"]],
                         [[0, 1], [1, 2]])
        state = self.ledger.status(self.job)
        params = dict(
            ledger=self.ledger, job_id=self.job, revision=state["revision"],
            previous_evidence_sha=state["stages"][0]["evidence_sha"],
            job_dir=self.dir / "job", normalized_audio=self.source,
            reviewer="mock-reviewer", review_reference="fake-listen-evidence",
        )
        decisions = [{
            "between_windows": seam["between_windows"],
            "left_keep_chars": len("復次文殊師利男身具七寶"),
            "right_drop_chars": 2,
            "reviewed_audio_start_ms": seam["audio_start_ms"],
            "reviewed_audio_end_ms": seam["audio_end_ms"],
            "audio_review_confirmed": True,
            "notes": "Synthetic operator declaration for offline testing only.",
        } for seam in inspection["seams"]]
        with self.assertRaises(Exception):
            finalize_reviewed_primary_overlaps(**params, seam_decisions=decisions[:1])
        self.assertEqual(self.ledger.status(self.job)["status"], "NEEDS_REVIEW")
        result = finalize_reviewed_primary_overlaps(**params, seam_decisions=decisions)
        self.assertTrue(result["ready_for_chirp"])
        self.assertEqual(self.vertex.models.generate_content.call_count, 3)
        with self.assertRaises(V2Conflict):
            finalize_reviewed_primary_overlaps(**params, seam_decisions=decisions)

    def test_audio_review_resumes_paid_primary_without_any_additional_provider_call(self):
        report = self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        self.assertEqual(len(report["windows"]), 2)
        state = self.ledger.status(self.job)
        original_sha = state["stages"][0]["evidence_sha"]
        before_calls = self.vertex.models.generate_content.call_count
        self.assertEqual(before_calls, 2)
        seam = {"between_windows": [0, 1], "left_keep_chars": len("復次文殊師利男身具七寶"),
                "right_drop_chars": 2, "reviewed_audio_start_ms": 2000,
                "reviewed_audio_end_ms": 2500, "audio_review_confirmed": True,
                "notes": "Test reviewer verified the 2.0–2.5s seam from audio."}
        options = dict(ledger=self.ledger, job_id=self.job,
                       revision=state["revision"], previous_evidence_sha=original_sha,
                       job_dir=self.dir / "job", normalized_audio=self.source,
                       seam_decisions=[seam],
                       reviewer="authenticated-test-reviewer",
                       review_reference="test-recording-2s-2_5s")
        bad = {**seam, "reviewed_audio_start_ms": 2200}
        with self.assertRaises(Exception):
            finalize_reviewed_primary_overlaps(**{**options, "seam_decisions": [bad]})
        self.assertEqual(self.ledger.status(self.job)["status"], "NEEDS_REVIEW")
        result = finalize_reviewed_primary_overlaps(**options)
        self.assertTrue(result["ready_for_chirp"])
        self.assertTrue((self.dir/"job/golden-v2/primary-stage.json").is_file())
        self.assertEqual(self.vertex.models.generate_content.call_count, before_calls)
        after = self.ledger.status(self.job)
        self.assertEqual(after["spent_cents"], 2)
        self.assertEqual(after["stages"][0]["state"], "COMPLETE")
        self.assertEqual(after["stages"][0]["evidence_sha"], result["reviewed_sha256"])
        with self.assertRaises(V2Conflict):
            finalize_reviewed_primary_overlaps(**options)
        self.assertEqual(self.ledger.claim_next(self.job, worker_id="next")["stage"],
                         "chirp_independent")

    def test_modified_raw_gemini_window_cannot_be_approved(self):
        self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        state = self.ledger.status(self.job)
        audit = next((self.dir/"job/gemini-primary").glob("*.json"))
        audit.write_text("{}", encoding="utf-8")
        with self.assertRaises(V2Conflict):
            finalize_reviewed_primary_overlaps(
                ledger=self.ledger, job_id=self.job, revision=state["revision"],
                previous_evidence_sha=state["stages"][0]["evidence_sha"],
                job_dir=self.dir/"job", normalized_audio=self.source,
                seam_decisions=[],
                reviewer="test-owner", review_reference="test-evidence",
            )
        self.assertEqual(self.ledger.status(self.job)["status"], "NEEDS_REVIEW")

    def test_reviewed_primary_comparison_requires_original_immutable_evidence(self):
        self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        state = self.ledger.status(self.job)
        seam = {
            "between_windows": [0, 1], "left_keep_chars": len("復次文殊師利男身具七寶"),
            "right_drop_chars": 2, "reviewed_audio_start_ms": 2000,
            "reviewed_audio_end_ms": 2500, "audio_review_confirmed": True,
            "notes": "Mock reviewer listened to the complete matching audio seam.",
        }
        finalize_reviewed_primary_overlaps(
            ledger=self.ledger, job_id=self.job, revision=state["revision"],
            previous_evidence_sha=state["stages"][0]["evidence_sha"],
            job_dir=self.dir / "job", normalized_audio=self.source,
            seam_decisions=[seam],
            reviewer="mock-owner", review_reference="mock-seam-evidence",
        )
        self.assertEqual(self.vertex.models.generate_content.call_count, 2)
        chirp_claim = self.ledger.claim_next(self.job, worker_id="mock-chirp")
        self.assertEqual(chirp_claim["stage"], "chirp_independent")
        merged = {
            "schema": "golden-v2-chirp-merged-v1", "job_id": self.job,
            "source_sha": self.sha, "timing_authority": "chirp_3",
            "words": [{"word": "復次文殊師利男身具七寶", "start_ms": 0,
                       "end_ms": 4000}],
        }
        wire = json.dumps(merged, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode()
        merged_path = self.dir / "job/golden-v2/chirp-merged.json"
        merged_path.write_bytes(wire)
        self.ledger.complete(chirp_claim, evidence_sha=hashlib.sha256(wire).hexdigest(),
                             actual_cents=0)
        compare_claim = self.ledger.claim_next(self.job, worker_id="mock-compare")
        self.assertEqual(compare_claim["stage"], "compare")
        original_path = self.dir / "job/golden-v2/primary-stage.json"
        original_bytes = original_path.read_bytes()
        original_path.write_text("{corrupt", encoding="utf-8")
        with self.assertRaises(V2Conflict):
            complete_comparison(ledger=self.ledger, claim=compare_claim,
                                job_dir=self.dir / "job")
        original_path.write_bytes(original_bytes)
        report = complete_comparison(ledger=self.ledger, claim=compare_claim,
                                     job_dir=self.dir / "job")
        self.assertEqual(report["primary_evidence_sha256"],
                         self.ledger.status(self.job)["stages"][0]["evidence_sha"])
        self.assertEqual(self.vertex.models.generate_content.call_count, 2)

    def test_changed_audio_cannot_be_declared_reviewed(self):
        self.execute(max_window_seconds=2.5, overlap_seconds=0.5)
        state = self.ledger.status(self.job)
        self.source.write_bytes(b"tampered normalized audio")
        with self.assertRaises(V2Conflict):
            finalize_reviewed_primary_overlaps(
                ledger=self.ledger, job_id=self.job, revision=state["revision"],
                previous_evidence_sha=state["stages"][0]["evidence_sha"],
                job_dir=self.dir / "job", normalized_audio=self.source,
                seam_decisions=[],
                reviewer="mock-owner", review_reference="mock-reference",
            )
        self.assertEqual(self.ledger.status(self.job)["status"], "NEEDS_REVIEW")

    def test_cost_preflight_excess_blocks_all_cloud_and_model_calls(self):
        with self.assertRaises(V2Conflict):
            self.execute(cost_estimator=lambda manifest: 99999)
        self.assertEqual(self.vertex.models.generate_content.call_count, 0)
        self.assertEqual(len(self.cloud.objects), 0)
        self.assertEqual(self.ledger.status(self.job)["spent_cents"], 0)

    def test_wrong_drive_observation_blocks_cloud_before_request(self):
        wrong = {**self.observation, "downloaded_sha256": "0" * 64}
        with self.assertRaises(ValueError):
            self.execute(drive_observation=wrong)
        self.assertEqual(len(self.cloud.objects), 0)
        self.assertEqual(self.vertex.models.generate_content.call_count, 0)

    def test_source_version_change_blocks_reuse(self):
        # Persist valid source evidence using a no-cloud cost overrun check.
        with self.assertRaises(V2Conflict):
            self.execute(cost_estimator=lambda m: 10000)
        modified = {**self.observation, "version": "v2"}
        with self.assertRaises(ValueError):
            self.execute(drive_observation=modified)
        self.assertEqual(len(self.cloud.objects), 0)
        self.assertEqual(self.vertex.models.generate_content.call_count, 0)

    def test_network_unknown_is_not_automatically_retried(self):
        self.vertex.models.generate_content.side_effect = TimeoutError("disconnect")
        with self.assertRaises(Exception):
            self.execute()
        state = self.ledger.status(self.job)
        self.assertEqual(state["status"], "NEEDS_RECONCILIATION")
        with self.assertRaises(V2Conflict):
            self.ledger.claim_next(self.job, worker_id="second")
        self.assertEqual(self.vertex.models.generate_content.call_count, 1)


if __name__ == "__main__":
    unittest.main()
