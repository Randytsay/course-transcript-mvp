from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import retranscription_routes as routes


class RetranscriptionRouteContractTests(unittest.TestCase):
    def _job_dir(self, temporary: str) -> Path:
        root = Path(temporary) / "jobs"
        job_dir = root / "job-1"
        chunk = job_dir / "chunks" / "chunk-000"
        chunk.mkdir(parents=True)
        (job_dir / "chunk-plan.json").write_text(
            json.dumps(
                {
                    "chunks": [
                        {
                            "chunk_index": 0,
                            "source_start_ms": 0,
                            "source_end_ms": 900000,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        for name, payload in (
            (
                "manifest.json",
                {
                    "chunk_index": 0,
                    "source_start_ms": 0,
                    "source_end_ms": 900000,
                    "status": "SUCCEEDED",
                },
            ),
            (
                "words.json",
                {
                    "chunk_index": 0,
                    "words": [
                        {"word": "測試", "start_ms": 1000, "end_ms": 1500}
                    ],
                },
            ),
            (
                "partial-transcript.json",
                {
                    "chunkIndex": 0,
                    "sourceStartMs": 0,
                    "sourceEndMs": 900000,
                    "status": "SUCCEEDED",
                    "wordCount": 1,
                    "rawText": "測試",
                },
            ),
        ):
            (chunk / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        return root

    def _job(self) -> dict:
        return {
            "id": "job-1",
            "revision": 7,
            "status": "awaiting_review",
            "language_code": "cmn-Hant-TW",
            "processing_strategy": "DYNAMIC_BATCHING",
        }

    def _estimate(self) -> dict:
        return {
            "duration_ms": 900000,
            "billable_minutes": "15.00",
            "processing_strategy": "DYNAMIC_BATCHING",
            "chirp_usd_per_minute": "0.003",
            "estimated_cost_usd": "0.0619",
            "estimated_cost_twd": "1.99",
            "pricing_version": "test-pricing",
            "project_limit_usd": "200",
        }

    def test_preview_reads_nested_quality_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            jobs_dir = self._job_dir(temporary)
            quality_item = {
                "chunk_index": 0,
                "quality": {
                    "severity": "high",
                    "score": 6,
                    "suspicious": True,
                    "reasons": ["density_far_below_course_baseline"],
                },
                "metrics": {
                    "density_chars_per_min": 12.5,
                    "relative_density": 0.12,
                },
            }
            with (
                patch.object(routes, "JOBS_DIR", jobs_dir),
                patch.object(routes, "_job", return_value=self._job()),
                patch.object(routes, "_quality_entry", return_value=({}, quality_item)),
                patch.object(routes, "_estimate", return_value=self._estimate()),
                patch.object(routes, "_existing_candidate", return_value=None),
                patch.object(
                    routes,
                    "_budget_snapshot",
                    return_value={"project_limit_usd": "200"},
                ),
            ):
                payload = routes._preview("job-1", 7, 0)

            self.assertEqual(payload["quality"]["severity"], "high")
            self.assertEqual(payload["quality"]["score"], 6)
            self.assertEqual(
                payload["quality"]["reasons"],
                ["density_far_below_course_baseline"],
            )
            self.assertEqual(payload["quality"]["metrics"]["relative_density"], 0.12)
            self.assertTrue(payload["recommended_for_retranscription"])
            self.assertTrue(payload["new_cost_reservation_required"])
            self.assertFalse(payload["paid_operation_started"])

    def test_existing_idempotent_candidate_does_not_reserve_estimate_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            jobs_dir = self._job_dir(temporary)
            quality_item = {
                "chunk_index": 0,
                "quality": {
                    "severity": "medium",
                    "score": 3,
                    "reasons": ["density_below_course_baseline"],
                },
                "metrics": {},
            }
            existing = {
                "id": "candidate-1",
                "job_id": "job-1",
                "source_revision": 7,
                "chunk_index": 0,
                "recognizer": "chirp_3",
                "language_code": "cmn-Hant-TW",
                "processing_strategy": "DYNAMIC_BATCHING",
                "estimated_cost_usd": "0.0619",
                "confirmed_cost_usd": "0.0619",
                "pricing_version": "test-pricing",
                "status": "queued",
                "requested_by": "tester",
                "requested_at": "2026-08-23T00:00:00+00:00",
                "updated_at": "2026-08-23T00:00:00+00:00",
                "submitted_at": None,
                "completed_at": None,
                "failed_at": None,
                "rejected_at": None,
                "error_kind": None,
                "error_safe_message": None,
            }
            seen: list[object] = []

            def budget(value=None):
                seen.append(value)
                return {"project_limit_usd": "200"}

            with (
                patch.object(routes, "JOBS_DIR", jobs_dir),
                patch.object(routes, "_job", return_value=self._job()),
                patch.object(routes, "_quality_entry", return_value=({}, quality_item)),
                patch.object(routes, "_estimate", return_value=self._estimate()),
                patch.object(routes, "_existing_candidate", return_value=existing),
                patch.object(routes, "_budget_snapshot", side_effect=budget),
            ):
                payload = routes._preview("job-1", 7, 0)

            self.assertEqual(seen, [None])
            self.assertFalse(payload["new_cost_reservation_required"])
            self.assertEqual(payload["existing_candidate"]["id"], "candidate-1")


if __name__ == "__main__":
    unittest.main()
