"""Provider-free immutable acoustic-review verification used by PR159 reentry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.pipeline.golden_v2_chirp_repair import verified_early_chirp_acoustic_evidence


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class EarlyAcousticEvidenceTests(unittest.TestCase):
    def test_source_raw_candidate_review_hashes_and_tampering(self):
        with TemporaryDirectory() as temp:
            job = "v2p_" + "a" * 32
            directory = Path(temp) / job
            directory.mkdir()
            media = b"fake-normalized-FLAC"
            source_sha = sha(media)
            (directory / "normalized.flac").write_bytes(media)
            golden = directory / "golden-v2"
            golden.mkdir()
            (golden / "source-lineage.json").write_bytes(
                wire({"normalized_sha256": source_sha}))
            window = dict(
                job_id=job, window_id="chunk-0000", source_sha=source_sha,
                clip_sha="c" * 64, operation_name="operations/persisted-001",
                request_id="r" * 64, status="DONE", purpose="BASE",
                start_ms=0, end_ms=12000, actual_cents=3,
            )
            raw = b"fake-immutable-provider-result"
            window["result_sha"] = sha(raw)
            evidence = golden / "chirp-evidence" / window["window_id"]
            evidence.mkdir(parents=True)
            (evidence / "provider-raw.json").write_bytes(raw)
            (evidence / "window.json").write_bytes(wire({
                "raw_sha": window["result_sha"],
                "source_sha": source_sha,
                "clip_sha": window["clip_sha"],
                "operation_name": window["operation_name"],
                "request_id": window["request_id"],
                "start_ms": 0, "end_ms": 12000,
                "actual_cents": 3,
            }))
            candidate = {"schema": "golden-v2-chirp-merged-v1",
                         "source_sha": source_sha, "words": [],
                         "acoustic_gate": "NOT_RUN"}
            candidate_sha = sha(wire(candidate))
            (golden / f"chirp-merged-candidate-{candidate_sha[:20]}.json").write_bytes(
                wire(candidate))
            acoustic = {"schema": "golden-v2-acoustic-qa-v1",
                        "status": "REVIEW", "source_sha256": source_sha,
                        "merged_sha256": candidate_sha}
            acoustic_sha = sha(wire(acoustic))
            report_path = golden / f"chirp-acoustic-qa-{acoustic_sha[:20]}.json"
            report_path.write_bytes(wire(acoustic))
            merged = {"schema": "golden-v2-chirp-merged-v1",
                      "job_id": job, "source_sha": source_sha,
                      "actual_cents": 3, "timing_authority": "chirp_3",
                      "acoustic_gate": "REVIEW", "golden_pass": False,
                      "acoustic_qa_sha256": acoustic_sha,
                      "window_result_shas": [window["result_sha"]]}
            merged_path = golden / "chirp-merged.json"
            merged_path.write_bytes(wire(merged))
            proof = verified_early_chirp_acoustic_evidence(directory)
            args = dict(job_id=job, source_sha=source_sha,
                        old_chirp_sha=sha(merged_path.read_bytes()),
                        old_acoustic_sha=acoustic_sha, windows=[window])
            self.assertTrue(proof(**args))
            self.assertFalse(proof(**{**args, "source_sha": "b" * 64}))
            self.assertFalse(proof(**{**args, "old_acoustic_sha": "0" * 64}))
            self.assertFalse(proof(**{**args, "windows": [
                {**window, "actual_cents": 4}]}))
            # A changed local receipt is never silently accepted as old proof.
            receipt = evidence / "window.json"
            original = receipt.read_bytes()
            receipt.write_bytes(original.replace(b"persisted-001", b"fake-replaced"))
            self.assertFalse(proof(**args))
            receipt.write_bytes(original)
            self.assertTrue(proof(**args))
            merged_path.write_bytes(wire({**merged, "golden_pass": True}))
            self.assertFalse(proof(**args))


if __name__ == "__main__":
    unittest.main()
