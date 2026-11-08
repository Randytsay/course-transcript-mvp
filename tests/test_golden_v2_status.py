"""Provider-free V2 read-only status CLI contract."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from scripts.golden_v2_status import read_v2_status


class GoldenV2StatusTests(unittest.TestCase):
    def test_status_has_no_approval_claim_or_secret_and_never_initializes_missing_db(self):
        with TemporaryDirectory() as tmp:
            database = Path(tmp) / "v2.db"
            with self.assertRaises(ValueError):
                read_v2_status(database, job_id="v2_absent")
            self.assertFalse(database.exists())
            ledger = GoldenV2Ledger(database)
            job = ledger.create(
                source_sha="a"*64, mode="QUALITY_3WAY",
                estimated_stage_cents={
                    "gemini_primary": 20, "chirp_independent": 25,
                    "compare": 0, "third_optional": 5,
                    "adjudication": 0, "golden_qa": 0,
                },
            )
            ledger.approve(job, revision=0, approved_cents=50,
                           actor="test", reference="PRIVATE_APPROVAL_REFERENCE")
            claim = ledger.claim_next(job, worker_id="mock")
            record = read_v2_status(database, job_id=job)
            self.assertEqual(record["stages"][0]["state"], "RUNNING")
            self.assertEqual(record["job"]["approved_cents"], 50)
            self.assertEqual(record["production_deployed"], "UNVERIFIED")
            self.assertNotIn("PRIVATE_APPROVAL_REFERENCE", str(record))
            self.assertNotIn(claim["claim"], str(record))
            self.assertNotIn("claim_hash", str(record))
            with self.assertRaises(ValueError):
                read_v2_status(database, job_id="v2_not_found")


if __name__ == "__main__":
    unittest.main()
