"""No-cost integration between the durable ledger and Gemini 3.8 audio adapter."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
import hashlib

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.providers.gemini_primary_audio import PrimaryTranscriptError, run_window


class ProviderApprovalBindingTests(TestCase):
    def test_real_ledger_approval_binding_and_restart_prevent_duplicate_calls(self):
        with TemporaryDirectory() as root:
            root_path = Path(root)
            ledger = GoldenV2Ledger(root_path / "data.db")
            job_id = ledger.create(
                source_sha="c"*64, mode="FLASH_CHIRP",
                estimated_stage_cents={
                    "gemini_primary": 5, "chirp_independent": 10, "compare": 0,
                    "third_optional": 0, "adjudication": 0, "golden_qa": 0,
                },
            )
            ledger.approve(job_id, revision=0, approved_cents=15,
                           reference="mock-no-real-spend", actor="owner-test")
            claim = ledger.claim_next(job_id, worker_id="v2-mock")
            window = {
                "index": 0, "start_seconds": 0, "end_seconds": 60,
                "gcs_uri": "gs://private-bucket/audio-source-window.wav",
                "mime_type": "audio/wav",
            }
            fake = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(
                return_value=SimpleNamespace(
                    text="復次文殊師利男身具七寶",
                    candidates=[SimpleNamespace(finish_reason="STOP")],
                    usage_metadata={"prompt_token_count": 1, "candidates_token_count": 1},
                )
            )))
            args = {
                "client": fake, "staged_window": window, "source_sha256": "c"*64,
                "job_dir": root_path / "job", "approval_token": claim["claim"],
                "part_factory": lambda uri, mime: (uri, mime),
                "approval_validator": ledger.provider_approval_validator(claim),
            }
            # The approval exists, but the window must be registered exactly.
            with self.assertRaises(PrimaryTranscriptError):
                run_window(**args)
            fake.models.generate_content.assert_not_called()

            ledger.register_window(claim, window)
            first = run_window(**args)
            self.assertEqual(first["status"], "COMPLETE")
            reloaded = GoldenV2Ledger(root_path / "data.db")
            args["approval_validator"] = reloaded.provider_approval_validator(claim)
            second = run_window(**args)
            self.assertEqual(first, second)
            fake.models.generate_content.assert_called_once()

            audited_path = next((root_path / "job/gemini-primary").glob("*.json"))
            evidence_sha = hashlib.sha256(audited_path.read_bytes()).hexdigest()
            reloaded.complete(claim, evidence_sha=evidence_sha, actual_cents=0)
            self.assertEqual(reloaded.status(job_id)["stages"][0]["state"], "COMPLETE")
            with self.assertRaises(PrimaryTranscriptError):
                run_window(**args)
            fake.models.generate_content.assert_called_once()


if __name__ == "__main__":
    import unittest
    unittest.main()
