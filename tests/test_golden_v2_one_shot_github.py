"""No-charge acceptance checks for 240s immutable Google Cloud smoke."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "scripts" / "golden_v2_one_shot_github.py"
spec = importlib.util.spec_from_file_location("v2_first_gemini38", FILE)
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)

class FakeBlob:
    def __init__(self, raw=b""):
        self.raw = raw
        self.calls = []
    def download_as_bytes(self, **kwargs):
        return self.raw
    def upload_from_string(self, raw, **kwargs):
        self.calls.append(kwargs)

class FakeBucket:
    def __init__(self, blob):
        self.blob_value=blob
    def blob(self, name):
        return self.blob_value

class FirstPaidPrimaryTests(unittest.TestCase):
    def test_scope_and_price_no_request(self):
        self.assertEqual(trial.STAGE_RESERVED_CENTS,200)
        self.assertEqual(trial.MAX_TOTAL_CENTS,1000)
        self.assertEqual(trial.MODEL,"gemini-3.8-flash")
        self.assertEqual(trial.token_cost_cents({
            "prompt_token_count":3000,
            "candidates_token_count":600,
            "thoughts_token_count":100
        }),1)
        with self.assertRaisesRegex(RuntimeError,"incomplete"):
            trial.token_cost_cents({
                "prompt_token_count":1000,"candidates_token_count":0
            })
        with self.assertRaises(RuntimeError):
            trial.token_cost_cents({
                "prompt_token_count":-1,"candidates_token_count":500
            })
    def test_claim_persist_before_any_provider_model(self):
        blob=FakeBlob()
        trial.record_once(FakeBucket(blob),"claim.json",{"status":"REQUEST_STARTED"})
        self.assertEqual(len(blob.calls),1)
        self.assertEqual(blob.calls[0]["if_generation_match"],0)
        self.assertIsNone(blob.calls[0]["retry"])
    def test_gcs_audio_sha_blocks_wrong_bytes(self):
        with self.assertRaisesRegex(RuntimeError,"SHA mismatch"):
            trial.verify_clip(FakeBucket(FakeBlob(b"wrong audio")))
    def test_untrusted_context_blocks(self):
        with patch.dict(os.environ,{
            "GITHUB_REPOSITORY":"evil/clone","GITHUB_REF":"refs/heads/main"
        }):
            with self.assertRaisesRegex(RuntimeError,"unauthorized"):
                trial.context_is_exact()

if __name__ == "__main__":
    unittest.main()
