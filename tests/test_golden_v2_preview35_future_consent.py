"""Unit checks for frozen, separate Gemini 3.5 preview candidate."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

P = Path(__file__).resolve().parents[1] / "scripts" / "golden_v2_preview35_future_consent.py"
spec = importlib.util.spec_from_file_location("golden_preview35_future", P)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)

class PreviewCandidateTests(unittest.TestCase):
    def test_model_and_claim_are_distinct_from_both_failed_ga(self):
        self.assertEqual(candidate.MODEL, "gemini-3.5-transcribe-preview")
        self.assertEqual(candidate.BYTES, 7680156)
        self.assertEqual(candidate.STAGE_CENTS, 200)
        self.assertEqual(candidate.TOTAL_CENTS, 1000)
        self.assertIn("preview-future-consent",candidate.CLAIM_KEY)
        self.assertNotEqual(candidate.CLAIM_KEY,candidate.FOLDER+"third-gemini35.claim.json")
        self.assertNotEqual(candidate.CLAIM_KEY,candidate.FOLDER+"third-gemini35-new-owner-approved-20261013.claim.json")
    def test_context_does_not_allow_unauthorized_paid_calls(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError,"untrusted GitHub identity"):
                candidate.validate_github_identity()
    def test_cloud_receipts_require_unique_keys(self):
        self.assertIn("preview-future-consent",candidate.RECEIPT_KEY)
        self.assertTrue(candidate.CLAIM_KEY.startswith(candidate.FOLDER))
if __name__=="__main__":
    unittest.main()
