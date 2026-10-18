import hashlib
import json
import unittest
from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_chirp_local_clock_fix import parse_local_clock_fix_candidate

class TestLocalClockFixModule(unittest.TestCase):
    def test_parse_valid_words(self):
        raw = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "測試", "startOffset": "0s", "endOffset": "0.5s"},
                        {"word": "音檔", "startOffset": "0.5s", "endOffset": "1.0s"},
                    ]
                }]
            }]
        }
        raw_bytes = json.dumps(raw).encode("utf-8")
        window = {
            "job_id": "v2p_0123456789abcdef0123456789abcdef",
            "window_id": "chunk-0000",
            "source_sha": "a" * 64,
            "clip_sha": "b" * 64,
            "operation_name": "op-test",
            "request_id": "req-test",
            "output_uri": "gs://test/out/",
            "start_ms": 0,
            "end_ms": 10000,
            "actual_cents": 5,
            "result_sha": hashlib.sha256(raw_bytes).hexdigest(),
        }
        res = parse_local_clock_fix_candidate(raw_bytes, window)
        self.assertEqual(res["fixable_count"], 0)
        self.assertEqual(len(res["manifest"]["words"]), 2)
        self.assertEqual(res["manifest"]["words"][0]["start_ms"], 0)
        self.assertEqual(res["manifest"]["words"][0]["end_ms"], 500)

    def test_coalesced_zero_duration(self):
        raw = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "地", "startOffset": "1.0s"},
                        {"word": "啊", "startOffset": "1.0s"},
                        {"word": "。", "startOffset": "1.0s", "endOffset": "1.2s"},
                    ]
                }]
            }]
        }
        raw_bytes = json.dumps(raw).encode("utf-8")
        window = {
            "job_id": "v2p_0123456789abcdef0123456789abcdef",
            "window_id": "chunk-0001",
            "source_sha": "a" * 64,
            "clip_sha": "b" * 64,
            "operation_name": "op-test",
            "request_id": "req-test",
            "output_uri": "gs://test/out/",
            "start_ms": 1000,
            "end_ms": 10000,
            "actual_cents": 5,
            "result_sha": hashlib.sha256(raw_bytes).hexdigest(),
        }
        res = parse_local_clock_fix_candidate(raw_bytes, window)
        self.assertGreater(res["fixable_count"], 0)
        words = res["manifest"]["words"]
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["word"], "地啊。")
        self.assertEqual(words[0]["start_ms"], 2000)
        self.assertEqual(words[0]["end_ms"], 2200)

    def test_rejection_over_50_percent(self):
        raw = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "壞", "endOffset": "0.5s"}, # head without start: 1 fix
                        {"word": "爛"}, # unusable: 1 unresolvable
                    ]
                }]
            }]
        }
        raw_bytes = json.dumps(raw).encode("utf-8")
        window = {
            "job_id": "v2p_0123456789abcdef0123456789abcdef",
            "window_id": "chunk-0002",
            "source_sha": "a" * 64,
            "clip_sha": "b" * 64,
            "operation_name": "op-test",
            "request_id": "req-test",
            "output_uri": "gs://test/out/",
            "start_ms": 0,
            "end_ms": 10000,
            "actual_cents": 5,
            "result_sha": hashlib.sha256(raw_bytes).hexdigest(),
        }
        with self.assertRaises(V2Conflict):
            parse_local_clock_fix_candidate(raw_bytes, window)

if __name__ == "__main__":
    unittest.main()
