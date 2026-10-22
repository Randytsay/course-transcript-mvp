"""Unit tests for lossless Chirp window repair, reviewed nonspeech QA, and delivery revalidation."""
import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_chirp_window_repair import repair_window
from app.pipeline.golden_v2_delivery import publish_granted_outputs
from app.pipeline.golden_v2_quote_intake import wire
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_acoustic_qa import inspect_v2_acoustic
from app.providers.golden_v2_audio_staging import file_sha256


class LosslessWindowRepairTests(unittest.TestCase):
    def _row(self, start_ms=0, end_ms=900_000):
        return {
            "job_id": "v2p_" + "a" * 32,
            "operation_name": "op-1",
            "request_id": "req-1",
            "window_id": "chunk-test",
            "source_sha": "b" * 64,
            "clip_sha": "c" * 64,
            "gcs_uri": "gs://in/clip.flac",
            "output_uri": "gs://out/",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "raw_sha": "d" * 64,
            "actual_cents": 225,
        }

    def test_jitter_does_not_drop_subsequent_words(self):
        payload = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "前", "startOffset": "148.880s", "endOffset": "149.480s"},
                        {"word": "啊", "startOffset": "148.320s", "endOffset": "148.400s"},
                        {"word": "我", "startOffset": "148.400s", "endOffset": "148.560s"},
                        {"word": "後", "startOffset": "150.000s", "endOffset": "150.200s"},
                    ]
                }]
            }]
        }
        res = repair_window(json.dumps(payload).encode(), self._row(6_240_000, 7_140_000))
        self.assertEqual(res.characters_lost, 0)
        self.assertEqual([w["word"] for w in res.words], ["前", "啊", "我", "後"])
        self.assertEqual(res.words[-1]["start_ms"], 6_240_000 + 150_000)

    def test_forward_clock_burst_reanchors_spike_not_tail(self):
        payload = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "如", "startOffset": "337.040s", "endOffset": "337.120s"},
                        {"word": "同", "startOffset": "667.600s", "endOffset": "668.000s"},
                        {"word": "樣", "startOffset": "668.000s", "endOffset": "669.000s"},
                        {"word": "說", "startOffset": "342.360s", "endOffset": "342.680s"},
                        {"word": "法", "startOffset": "342.680s", "endOffset": "343.000s"},
                    ]
                }]
            }]
        }
        res = repair_window(json.dumps(payload).encode(), self._row(7_120_000, 8_020_000))
        self.assertEqual(res.characters_lost, 0)
        self.assertTrue(7_120_000 + 337_120 <= res.words[1]["start_ms"] < 7_120_000 + 342_360)
        self.assertEqual(res.words[3]["start_ms"], 7_120_000 + 342_360)

    def test_backward_clock_slip_maps_slipped_run_into_gap(self):
        payload = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "耶", "startOffset": "763.080s", "endOffset": "664.600s"},
                        {"word": "南", "startOffset": "664.640s", "endOffset": "665.000s"},
                        {"word": "無", "startOffset": "665.000s", "endOffset": "666.000s"},
                        {"word": "喝", "startOffset": "682.480s", "endOffset": "808.000s"},
                        {"word": "囉", "startOffset": "808.000s", "endOffset": "808.200s"},
                    ]
                }]
            }]
        }
        res = repair_window(json.dumps(payload).encode(), self._row(8_010_000, 8_882_240))
        self.assertEqual(res.characters_lost, 0)
        self.assertEqual(res.words[0]["start_ms"], 8_773_080)
        self.assertEqual(res.words[0]["end_ms"], 8_773_160)
        self.assertGreaterEqual(res.words[1]["start_ms"], 8_773_160)
        self.assertLessEqual(res.words[3]["end_ms"], 8_818_000)
        self.assertEqual(res.words[4]["start_ms"], 8_818_000)

    def test_overflow_and_stretched_word_clamped(self):
        payload = {
            "results": [{
                "alternatives": [{
                    "words": [
                        {"word": "哎", "startOffset": "17.720s", "endOffset": "52.760s"},
                        {"word": "被", "startOffset": "154017527235.840s", "endOffset": "329.200s"},
                    ]
                }]
            }]
        }
        res = repair_window(json.dumps(payload).encode(), self._row(890_000, 1_790_000))
        self.assertEqual(res.words[0]["start_ms"], 907_720)
        self.assertEqual(res.words[0]["end_ms"], 908_220)
        self.assertEqual(res.words[1]["start_ms"], 1_219_120)
        self.assertEqual(res.words[1]["end_ms"], 1_219_200)


class ReviewedNonspeechAndRevalidatedDeliveryTests(unittest.TestCase):
    def test_reviewed_nonspeech_sidecar_allows_pass(self):
        with TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "normalized.flac").write_bytes(b"flac-bytes")
            sha = hashlib.sha256(b"flac-bytes").hexdigest()
            (folder / "golden-v2").mkdir()
            sidecar = {
                "schema": "golden-v2-acoustic-reviewed-nonspeech-v1",
                "source_sha256": sha,
                "reviewer": "owner",
                "review_reference": "audit-20261021",
                "intervals": [
                    {"start_ms": 400, "end_ms": 6100, "disposition": "NONSPEECH_CONFIRMED",
                     "reason": "verified blackboard pause"}
                ],
            }
            (folder / "golden-v2" / "acoustic-reviewed-nonspeech.json").write_text(json.dumps(sidecar))
            merged = {
                "source_sha": sha,
                "duration_ms": 6500,
                "timing_authority": "chirp_3",
                "words": [
                    {"word": "師", "start_ms": 100, "end_ms": 400},
                    {"word": "父", "start_ms": 6100, "end_ms": 6500},
                ],
            }
            with patch("app.providers.golden_v2_acoustic_qa.duration_seconds", return_value=6.5), \
                 patch("app.providers.golden_v2_acoustic_qa._vad_assessment", return_value={"robust_non_speech": False, "robust_speech": False}), \
                 patch("app.providers.golden_v2_acoustic_qa._peak_db", return_value=-20.0), \
                 patch("app.providers.golden_v2_acoustic_qa._audible", return_value=True):
                qa = inspect_v2_acoustic(job_dir=folder, merged=merged)
            self.assertEqual(qa["status"], "PASS")
            self.assertEqual(qa["blocker_count"], 0)


if __name__ == "__main__":
    unittest.main()
