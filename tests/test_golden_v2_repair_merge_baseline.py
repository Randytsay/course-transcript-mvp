"""Restarted Chirp repair merges prior verified words without reparsing bad old raw."""
import hashlib
import json
import sqlite3
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_chirp_stage import finish_approved_chirp
from app.providers.golden_v2_versioned_artifacts import resolve_versioned


def wire(d):
    return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class CompletedChirpRepairMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job_dir = self.root / "jobs" / "test-job"
        self.golden = self.job_dir / "golden-v2"
        self.golden.mkdir(parents=True)
        self.audio = self.job_dir / "normalized.flac"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=210:sample_rate=16000",
             "-t", "5", "-ac", "1", "-c:a", "flac", "-y", str(self.audio)],
            check=True, capture_output=True, timeout=30,
        )
        self.source_sha = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        self.ledger = GoldenV2Ledger(self.root / "ledger.sqlite")
        self.job = self.ledger.create(
            source_sha=self.source_sha, mode="FLASH_CHIRP",
            estimated_stage_cents=dict(
                gemini_primary=100, chirp_independent=30,
                compare=0, third_optional=0, adjudication=0, golden_qa=0,
            ),
        )
        self.job_dir = self.root / "jobs" / self.job
        self.golden = self.job_dir / "golden-v2"
        self.golden.mkdir(parents=True)
        self.audio.replace(self.job_dir / "normalized.flac")
        self.ledger.approve(self.job, revision=0, approved_cents=130,
                            reference="owner-unit", actor="tester")
        c=self.ledger.claim_next(self.job,worker_id="tester")
        self.ledger.complete(c,evidence_sha="a"*64,actual_cents=20)
        c=self.ledger.claim_next(self.job,worker_id="tester",
                                 expected_stage="chirp_independent")
        bad_raw=wire({"results":[{"alternatives":[{"words":[
            {"word":"不能猜的字","startOffset":"0.2s"}
        ]}]}]})
        self.bad_raw_sha=hashlib.sha256(bad_raw).hexdigest()
        self.base_req="1"*64
        self.add_record("chunk-0000", 0, 5000, "BASE", bad_raw, 30,
                        request_id=self.base_req)
        prior={"schema":"golden-v2-chirp-merged-v1","job_id":self.job,
            "source_sha":self.source_sha,"duration_ms":5000,
            "window_result_shas":[self.bad_raw_sha],"repair_result_shas":[],
            "words":[{"word":"佛","start_ms":1000,"end_ms":1200},
                     {"word":"法","start_ms":4000,"end_ms":4200}],
            "actual_cents":30,"timing_authority":"chirp_3",
            "acoustic_gate":"PASS","golden_pass":False}
        prior_bytes=wire(prior)
        self.prior_sha=hashlib.sha256(prior_bytes).hexdigest()
        (self.golden/"chirp-merged.json").write_bytes(prior_bytes)
        self.ledger.complete(c,evidence_sha=self.prior_sha,actual_cents=30)
        c=self.ledger.claim_next(self.job,worker_id="tester",expected_stage="compare")
        self.ledger.complete(c,evidence_sha="b"*64,actual_cents=0,
                             comparison_has_dispute=False)
        c=self.ledger.claim_next(self.job,worker_id="tester",
                                 expected_stage="adjudication")
        self.ledger.complete(c,evidence_sha="c"*64,actual_cents=0)
        c=self.ledger.claim_next(self.job,worker_id="tester",
                                 expected_stage="golden_qa")
        self.ledger.hold_for_review(c,evidence_sha="d"*64)
        self.rev=self.ledger.status(self.job)["revision"]

    def add_record(self, wid, start, end, purpose, raw, cents, request_id):
        proof=self.golden/"chirp-evidence"/wid
        proof.mkdir(parents=True)
        sha=hashlib.sha256(raw).hexdigest()
        (proof/"provider-raw.json").write_bytes(raw)
        (proof/"window.json").write_bytes(wire({
            "raw_sha":sha,"actual_cents":cents,"request_id":request_id,
        }))
        with self.ledger._tx() as db:
            db.execute(
                "INSERT INTO v2_chirp_operations "
                "(job_id,window_id,source_sha,clip_sha,gcs_uri,start_ms,"
                "end_ms,status,operation_name,request_id,output_uri,"
                "estimated_cents,purpose,result_sha,actual_cents,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.job,wid,self.source_sha,"3"*64,
                 "gs://test/input/"+wid,start,end,
                 "DONE","projects/fake/locations/us/operations/"+wid,
                 request_id,"gs://test/out/"+wid+"/",
                 cents,purpose,sha,cents,"2026-10-20T11:00:00+08:00"),
            )

    def resume(self):
        return self.ledger.resume_chirp_review_for_repair(
            self.job, revision=self.rev, source_sha=self.source_sha,
            previous_chirp_evidence_sha=self.prior_sha,
            previous_qa_evidence_sha="d"*64,
            transfer_cents=10,reviewer="owner",review_reference="test-review",
            worker_id="test-worker",aggregate_cap_cents=1000,
            evidence_verifier=lambda **_: True)

    @staticmethod
    def passing_inspector(*,job_dir,merged):
        return {
            "schema":"golden-v2-acoustic-qa-v1","status":"PASS",
            "source_sha256":merged["source_sha"],
            "merged_sha256":hashlib.sha256(wire(merged)).hexdigest(),
            "gaps_examined":[],"repair_plan":{"intervals":[]},
        }

    def test_preserves_unparseable_raw_baseline_and_writes_new_version(self):
        self.resume()
        raw=wire({"results":[{"alternatives":[{"words":[
            {"word":"僧","startOffset":"0.1s","endOffset":"0.3s"}
        ]}]}]})
        self.add_record("repair-r01-00",1900,2300,"REPAIR",raw,1,
                        request_id="4"*64)
        with self.ledger._tx() as db:
            db.execute(
                "UPDATE v2_chirp_operations SET target_intervals_json=? "
                "WHERE job_id=? AND window_id='repair-r01-00'",
                (json.dumps([{"start_ms":2000,"end_ms":2200}]),
                 self.job))
        result=finish_approved_chirp(
            ledger=self.ledger,job_id=self.job,job_dir=self.job_dir,
            acoustic_inspector=self.passing_inspector)
        self.assertEqual([w["word"] for w in result["words"]],["佛","僧","法"])
        self.assertEqual(self.ledger.status(self.job)["stages"][1]["state"],"COMPLETE")
        new_sha=self.ledger.status(self.job)["stages"][1]["evidence_sha"]
        self.assertNotEqual(new_sha,self.prior_sha)
        self.assertEqual(resolve_versioned(self.golden/"chirp-merged.json",new_sha).name,
                         "chirp-merged-"+new_sha+".json")
        self.assertEqual(
            hashlib.sha256((self.golden/"chirp-merged.json").read_bytes()).hexdigest(),
            self.prior_sha)
        self.assertEqual(result["actual_cents"],31)
        self.assertEqual(len(self.ledger.chirp_windows(self.job)),2)


if __name__=="__main__":
    unittest.main()