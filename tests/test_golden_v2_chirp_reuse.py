"""No-cost Chirp 3 evidence adoption from already-paid historical job."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import unittest

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.providers.golden_v2_audio_staging import file_sha256
from app.pipeline.golden_v2_chirp_reuse import ChirpReuseError, adopt_verified_chirp


def write(path: Path, payload: dict):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class GoldenV2ChirpReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.historical = self.root / "old"
        self.historical.mkdir()
        self.source = self.historical / "normalized.flac"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
             "-i", "sine=frequency=500:sample_rate=16000", "-t", "3",
             "-ac", "1", "-c:a", "flac", "-y", str(self.source)],
            check=True, capture_output=True, timeout=20,
        )
        write(self.historical / "merged-words.json",
              {"words":[{"word":"復次", "start_ms":100, "end_ms":500},
                        {"word":"文殊", "start_ms":500, "end_ms":1300}]})
        write(self.historical / "subtitles.json", {"segments":[
            {"segment_id":"seg-1","start_ms":100,"end_ms":1300,"raw_text":"復次文殊"}]})
        write(self.historical / "chirp-completeness.json",
              {"status":"PASS", "handoff_allowed":True, "blockers":[]})
        write(self.historical / "chirp-submitted.json", {"operations":["existing-chirp-operation"]})
        write(self.historical / "chunk-plan.json", {"duration_seconds":3.0})
        self.ledger = GoldenV2Ledger(self.root / "jobs.db")
        self.job = self.ledger.create(
            source_sha=file_sha256(self.source), mode="QUALITY_3WAY",
            estimated_stage_cents={
                "gemini_primary": 5, "chirp_independent": 10,
                "compare": 0, "third_optional": 3,
                "adjudication": 0, "golden_qa": 0,
            },
        )
        self.ledger.approve(self.job, revision=0, approved_cents=18,
                            reference="fake", actor="test-owner")
        primary = self.ledger.claim_next(self.job, worker_id="mock")
        self.ledger.complete(primary, evidence_sha="a"*64, actual_cents=1)
        self.claim = self.ledger.claim_next(self.job, worker_id="mock")
        self.assertEqual(self.claim["stage"], "chirp_independent")

    def tearDown(self):
        self.temp.cleanup()

    def adopt(self):
        return adopt_verified_chirp(
            ledger=self.ledger, claim=self.claim,
            historical_job_dir=self.historical,
            output_job_dir=self.root / "v2",
        )

    def test_reuses_matching_chirp_evidence_for_no_new_cost(self):
        report = self.adopt()
        self.assertEqual(report["new_provider_calls"], 0)
        self.assertEqual(report["new_chirp_actual_cents"], 0)
        self.assertEqual(self.ledger.status(self.job)["spent_cents"], 1)
        self.assertEqual(self.ledger.claim_next(self.job, worker_id="mock")["stage"], "compare")
        self.assertTrue((self.root/"v2/golden-v2/chirp-reuse.json").exists())

    def test_existing_acoustic_blocker_prevents_chirp_reuse(self):
        write(self.historical/"chirp-completeness.json",
              {"status":"BLOCKED", "handoff_allowed":False, "blockers":[{"reason":"speech_gap"}]})
        with self.assertRaises(ChirpReuseError):
            self.adopt()
        self.assertEqual(self.ledger.status(self.job)["stages"][1]["state"], "RUNNING")

    def test_changed_normalized_audio_sha_prevents_reuse(self):
        self.source.write_bytes(b"tampered")
        with self.assertRaises(ChirpReuseError):
            self.adopt()

    def test_missing_provider_operation_evidence_prevents_reuse(self):
        (self.historical/"chirp-submitted.json").unlink()
        with self.assertRaises(ChirpReuseError):
            self.adopt()

    def test_timeline_outside_source_duration_is_rejected(self):
        write(self.historical/"merged-words.json",
              {"words":[{"word":"錯字","start_ms":3000,"end_ms":6000}]})
        with self.assertRaises(ChirpReuseError):
            self.adopt()


if __name__ == "__main__":
    unittest.main()
