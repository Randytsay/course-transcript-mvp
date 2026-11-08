"""Synthetic regressions for WebRTC VAD dead-zone elimination and subwindow packing."""
from __future__ import annotations

import math
import unittest
from array import array
from unittest.mock import patch

from app.providers.chirp_completeness_gate import (
    _assess_pcm_vad,
    pack_speech_subwindows,
)


def _make_pcm(
    *,
    duration_ms: int,
    speech_intervals_ms: list[tuple[int, int]],
    speech_amplitude: int = 4000,
    silence_amplitude: int = 30,
    sample_rate: int = 16_000,
) -> bytes:
    """Generate deterministic 16kHz s16le PCM bytes with synthetic speech/silence."""
    total_samples = duration_ms * sample_rate // 1000
    samples = array("h", [0] * total_samples)
    for idx in range(total_samples):
        t_ms = idx * 1000 // sample_rate
        in_speech = any(start <= t_ms < end for start, end in speech_intervals_ms)
        if in_speech:
            # Multi-harmonic voiced-like waveform in human speech band (200Hz + 500Hz + 1000Hz)
            val = int(
                speech_amplitude
                * (
                    0.55 * math.sin(2 * math.pi * 210 * idx / sample_rate)
                    + 0.30 * math.sin(2 * math.pi * 530 * idx / sample_rate)
                    + 0.15 * math.sin(2 * math.pi * 1050 * idx / sample_rate)
                )
            )
        else:
            val = int(silence_amplitude * math.sin(2 * math.pi * 60 * idx / sample_rate))
        samples[idx] = max(-32767, min(32767, val))
    return samples.tobytes()


class _FakeVad:
    """Deterministic fake webrtcvad.Vad for unit testing frame-level logic."""

    def __init__(self, mode: int = 0) -> None:
        self.mode = mode

    def is_speech(self, buf: bytes, sample_rate: int) -> bool:
        arr = array("h")
        arr.frombytes(buf)
        msq = sum(int(x) * int(x) for x in arr) / max(1, len(arr))
        rms = math.sqrt(msq)
        if self.mode == 0:
            return rms > 120.0
        return rms > 600.0


class TestChirpVadDeadzoneAndSubsliceRepair(unittest.TestCase):
    def test_pause_diluted_30s_slice_rescued_from_deadzone(self) -> None:
        """A 30s window with 6s of clear speech (20% occupancy) must be robust_speech=True."""
        # 6 seconds of speech out of 30 seconds (aligned to 30ms frame boundaries)
        # -> mode0_ratio = 0.20, which used to fail both robust_non_speech (<0.15)
        # and robust_speech (>=0.45).
        pcm = _make_pcm(
            duration_ms=30_000,
            speech_intervals_ms=[(2_100, 5_100), (12_000, 15_000)],
            speech_amplitude=3500,
            silence_amplitude=20,
        )
        with patch("webrtcvad.Vad", _FakeVad):
            res = _assess_pcm_vad(pcm, start_ms=809_000)

        self.assertIsNotNone(res)
        assert res is not None
        self.assertAlmostEqual(res["mode0_speech_ratio"], 0.20, places=2)
        self.assertFalse(res["robust_non_speech"])
        self.assertTrue(res["robust_speech"])
        self.assertEqual(res["speech_segment_count"], 2)
        self.assertEqual(res["active_speech_ms"], 6000)
        self.assertGreaterEqual(res["max_rolling_1s_mode3_ratio"], 0.90)
        self.assertEqual(
            res["speech_segments_ms"],
            [
                {"start_ms": 811_100, "end_ms": 814_100},
                {"start_ms": 821_000, "end_ms": 824_000},
            ],
        )

    def test_ambient_pause_with_mode0_flutter_classified_as_nonspeech(self) -> None:
        """clip_05 archetype: mode0 ~0.19 from low-level room hum, mode3 <0.05, rms <= -35dBFS."""
        # 6 seconds of low-energy ambient flutter (amplitude 400 -> RMS ~182 -> mode0 True, mode3 False)
        # in a 30s quiet window -> overall RMS < -35 dBFS, mode3 = 0.0, active_speech_ms = 0.
        pcm = _make_pcm(
            duration_ms=30_000,
            speech_intervals_ms=[(1_200, 7_200)],
            speech_amplitude=400,
            silence_amplitude=20,
        )
        with patch("webrtcvad.Vad", _FakeVad):
            res = _assess_pcm_vad(pcm, start_ms=2_203_800)

        self.assertIsNotNone(res)
        assert res is not None
        self.assertGreaterEqual(res["mode0_speech_ratio"], 0.18)
        self.assertEqual(res["mode3_speech_ratio"], 0.0)
        self.assertLessEqual(res["rms_dbfs"], -35.0)
        self.assertEqual(res["active_speech_ms"], 0)
        self.assertTrue(res["robust_non_speech"])
        self.assertFalse(res["robust_speech"])

    def test_pack_speech_subwindows_excludes_long_silence_and_fits_budget(self) -> None:
        """A 410.2s gap with 3 speech clusters separated by 60s pauses packs tightly."""
        segments = [
            {"start_ms": 810_000, "end_ms": 845_000},
            {"start_ms": 848_000, "end_ms": 880_000},  # 3s gap -> merges with first
            {"start_ms": 950_000, "end_ms": 1_010_000},  # 70s silence -> separate window
            {"start_ms": 1_120_000, "end_ms": 1_215_000},  # 110s silence -> separate window
        ]
        windows = pack_speech_subwindows(
            segments,
            bounds_start_ms=809_000,
            bounds_end_ms=1_219_200,
            context_ms=2_500,
            merge_gap_ms=8_000,
            max_window_ms=180_000,
        )
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0]["start_ms"], 809_000)
        self.assertEqual(windows[0]["end_ms"], 882_500)
        self.assertEqual(windows[1]["start_ms"], 947_500)
        self.assertEqual(windows[1]["end_ms"], 1_012_500)
        self.assertEqual(windows[2]["start_ms"], 1_117_500)
        self.assertEqual(windows[2]["end_ms"], 1_217_500)
        total_ms = sum(w["end_ms"] - w["start_ms"] for w in windows)
        self.assertLess(total_ms, 260_000)


if __name__ == "__main__":
    unittest.main()
