from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException

from app import retranscription_routes as routes
from app.jobs import retranscription_worker_entry


class RetranscriptionFeatureGateTests(unittest.TestCase):
    def test_gate_defaults_off(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(routes._retranscription_enabled())
            self.assertFalse(retranscription_worker_entry.enabled())

    def test_gate_accepts_explicit_true_only(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"ASR_RETRANSCRIPTION_ENABLED": value}, clear=True
            ):
                self.assertTrue(routes._retranscription_enabled())
                self.assertTrue(retranscription_worker_entry.enabled())

    def test_create_fails_before_candidate_work_when_gate_is_off(self) -> None:
        payload = routes.CreateRetranscriptionCandidateRequest(
            expected_revision=1,
            chunk_index=0,
            confirmed_estimated_cost_usd=Decimal("0.0100"),
        )
        with (
            patch.dict(os.environ, {"ASR_RETRANSCRIPTION_ENABLED": "false"}),
            patch.object(routes, "_mutation_actor", return_value="tester"),
            patch.object(routes, "_preview") as preview,
        ):
            with self.assertRaises(HTTPException) as caught:
                routes.create_retranscription_candidate(
                    "job-1", payload, object()  # type: ignore[arg-type]
                )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("live gate", str(caught.exception.detail))
        preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
