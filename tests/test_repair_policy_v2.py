from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


class RepairPolicyV2Tests(unittest.TestCase):
    def test_batch_pool_budget_allows_repair_within_approved_ceiling(self) -> None:
        from app.pipeline import dynamic_worker_hardened as worker

        class FakeStore:
            def __init__(self, _path: Path) -> None:
                pass

            def get_batch(self, _batch_id: str):
                return {
                    "reserved_cost_usd": "18.7500",
                    "actual_cost_usd": "12.0000",
                    "jobs": [{"id": "a"}, {"id": "b"}],
                }

        accrued = {"target": Decimal("0.3000"), "a": Decimal("6.0000"), "b": Decimal("6.4000")}
        with tempfile.TemporaryDirectory() as temp, patch.object(
            worker, "JobStore", FakeStore
        ), patch.object(
            worker,
            "estimated_accrued_cost",
            side_effect=lambda _db, _data, job_id: accrued[job_id],
        ):
            allowed, committed, extra, ceiling = worker._targeted_patch_budget(
                {
                    "id": "target",
                    "batch_id": "batch-1",
                    "reserved_cost_usd": "0.4000",
                },
                data_dir=Path(temp),
                plan={"total_duration_ms": 600_000},
            )

        self.assertTrue(allowed)
        self.assertEqual(committed, Decimal("12.4000"))
        self.assertEqual(extra, Decimal("0.1600"))
        self.assertEqual(ceiling, Decimal("18.7500"))

    def test_batch_pool_budget_blocks_when_batch_reservation_released(self) -> None:
        from app.pipeline import dynamic_worker_hardened as worker

        class FakeStore:
            def __init__(self, _path: Path) -> None:
                pass

            def get_batch(self, _batch_id: str):
                return {
                    "reserved_cost_usd": "0",
                    "actual_cost_usd": "20.4022",
                    "jobs": [{"id": "target"}],
                }

        with tempfile.TemporaryDirectory() as temp, patch.object(
            worker, "JobStore", FakeStore
        ), patch.object(
            worker, "estimated_accrued_cost", return_value=Decimal("0.3000")
        ):
            allowed, committed, extra, ceiling = worker._targeted_patch_budget(
                {
                    "id": "target",
                    "batch_id": "batch-1",
                    "reserved_cost_usd": "1.0000",
                },
                data_dir=Path(temp),
                plan={"total_duration_ms": 60_000},
            )

        self.assertFalse(allowed)
        self.assertEqual(committed, Decimal("20.4022"))
        self.assertEqual(extra, Decimal("0.0160"))
        self.assertEqual(ceiling, Decimal("0"))

    def test_local_zero_word_tail_evidence_is_durable(self) -> None:
        from app.providers.chirp_completeness_gate import _local_fallback_zero_word_evidence

        with tempfile.TemporaryDirectory() as temp:
            job_dir = Path(temp)
            evidence_dir = job_dir / "local-asr-fallback"
            evidence_dir.mkdir()
            (evidence_dir / "tail-local-asr.json").write_text(
                json.dumps(
                    {
                        "start_ms": 9_711_640,
                        "end_ms": 9_713_490,
                        "duration_ms": 1_850,
                        "segments": [],
                        "text": "",
                        "provider_calls_started": False,
                    }
                ),
                encoding="utf-8",
            )
            evidence = _local_fallback_zero_word_evidence(
                job_dir, 9_711_640, 9_713_490
            )

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["lexical_word_count"], 0)
        self.assertFalse(evidence["provider_calls_started"])


if __name__ == "__main__":
    unittest.main()
