"""Review a saved provider response with an omitted final word end.

The recovery is a lossless, unpaid timestamp draft, never acoustic PASS.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.pipeline.golden_v2_chirp_stage import reconcile_reviewed_chirp_window


class MissingRepairEndTests(unittest.TestCase):
    def test_repair_window_missing_final_end_preserves_provider_words_and_operation(self):
        for purpose, expected in (("REPAIR", "DONE"), ("BASE", "REVIEW_REQUIRED")):
            with self.subTest(purpose=purpose), tempfile.TemporaryDirectory() as tmp:
                job_id = "v2p_" + "a" * 32
                window_id = "repair-r01-00"
                root = Path(tmp) / "golden-v2" / "chirp-evidence" / window_id
                root.mkdir(parents=True)
                raw = json.dumps({"results": [{"alternatives": [{"words": [
                    {"word": "佛", "startOffset": "0.400s", "endOffset": "0.720s"},
                    {"word": "法", "startOffset": "0.720s"},
                ]}]}]}, ensure_ascii=False).encode()
                raw_sha = hashlib.sha256(raw).hexdigest()
                row = {
                    "job_id": job_id, "window_id": window_id, "purpose": purpose,
                    "status": "REVIEW_REQUIRED",
                    "result_sha": raw_sha, "actual_cents": 1,
                    "operation_name": "existing-operation", "request_id": "saved-request",
                    "source_sha": "b" * 64, "clip_sha": "c" * 64,
                    "gcs_uri": "gs://existing/input", "output_uri": "gs://existing/output/",
                    "start_ms": 2000, "end_ms": 12_000,
                }
                review = {
                    "schema": "golden-v2-chirp-window-review-v1",
                    "job_id": job_id, "window_id": window_id,
                    "operation_name": row["operation_name"], "request_id": row["request_id"],
                    "source_sha": row["source_sha"], "clip_sha": row["clip_sha"],
                    "raw_sha": raw_sha, "actual_cents": 1,
                    "reason": "missing word end has no same-time successor",
                }
                (root / "provider-raw.json").write_bytes(raw)
                (root / "review-required.json").write_text(json.dumps(review))
                class Ledger:
                    called = False
                    def chirp_windows(self, job):
                        assert job == job_id
                        return [dict(row)]
                    def resolve_chirp_window_review(self, *args, **kwargs):
                        assert kwargs["actual_cents"] == 1
                        assert kwargs["operation_name"] == "existing-operation"
                        assert kwargs["verifier"](
                            evidence_sha=raw_sha, operation_name="existing-operation",
                            source_sha=row["source_sha"], clip_sha=row["clip_sha"],
                            gcs_uri=row["gcs_uri"], start_ms=2000, end_ms=12_000)
                        self.called = True
                ledger = Ledger()
                result = reconcile_reviewed_chirp_window(
                    ledger=ledger, job_id=job_id, window_id=window_id, job_dir=Path(tmp))
                self.assertEqual(result, expected)
                self.assertEqual(ledger.called, purpose == "REPAIR")
                if purpose == "REPAIR":
                    result_doc = json.loads((root / "window.json").read_text())
                    self.assertEqual(result_doc["lossless_repair"]["characters_lost"], 0)
                    self.assertFalse(result_doc["lossless_repair"]["acoustic_timing_verified"])
                    self.assertEqual("".join(w["word"] for w in result_doc["words"]), "佛法")


if __name__ == "__main__":
    unittest.main()
