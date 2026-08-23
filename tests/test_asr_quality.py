"""Tests for provider-free ASR chunk quality analysis."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.asr_quality import analyze_job, write_report


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_chunk(
    job: Path,
    index: int,
    *,
    duration_ms: int = 900_000,
    word_count: int = 120,
    status: str = "SUCCEEDED",
    speech_start_ratio: float = 0.02,
    speech_end_ratio: float = 0.98,
    repeated: bool = False,
    missing_words: bool = False,
) -> None:
    chunk = job / "chunks" / f"chunk-{index:03d}"
    chunk.mkdir(parents=True, exist_ok=True)
    start_ms = index * 1_000_000
    end_ms = start_ms + duration_ms
    write_json(chunk / "manifest.json", {
        "chunk_index": index,
        "role": "base",
        "source_start_ms": start_ms,
        "source_end_ms": end_ms,
        "status": status,
        "word_count": word_count,
    })

    if status == "EMPTY_SILENCE":
        write_json(chunk / "partial-transcript.json", {
            "chunkIndex": index,
            "sourceStartMs": start_ms,
            "sourceEndMs": end_ms,
            "status": status,
            "wordCount": 0,
            "rawText": "",
            "firstWordMs": None,
            "lastWordMs": None,
        })
        write_json(chunk / "words.json", {"chunk_index": index, "words": []})
        return

    speech_start = start_ms + int(duration_ms * speech_start_ratio)
    speech_end = start_ms + int(duration_ms * speech_end_ratio)
    span = max(1, speech_end - speech_start)
    words = []
    tokens = []
    for offset in range(word_count):
        if repeated:
            token = ("佛", "法", "僧")[offset % 3]
        else:
            token = f"詞{index}_{offset}"
        tokens.append(token)
        word_start = speech_start + int(span * offset / max(1, word_count))
        word_end = min(speech_end, word_start + max(20, int(span / max(1, word_count) * 0.6)))
        words.append({"word": token, "start_ms": word_start, "end_ms": word_end})

    write_json(chunk / "partial-transcript.json", {
        "chunkIndex": index,
        "sourceStartMs": start_ms,
        "sourceEndMs": end_ms,
        "status": status,
        "wordCount": word_count,
        "rawText": "".join(tokens),
        "firstWordMs": words[0]["start_ms"] if words else None,
        "lastWordMs": words[-1]["end_ms"] if words else None,
    })
    if not missing_words:
        write_json(chunk / "words.json", {"chunk_index": index, "words": words})


class TestASRQuality(unittest.TestCase):
    def _job(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        job = Path(temporary.name) / "jobs" / "course-a"
        (job / "chunks").mkdir(parents=True)
        return job

    def test_course_relative_low_density_chunk_is_flagged_high(self):
        job = self._job()
        for index in range(4):
            make_chunk(job, index, word_count=120)
        make_chunk(
            job, 4, word_count=10,
            speech_start_ratio=0.05, speech_end_ratio=0.18,
        )

        report = analyze_job(job)
        by_index = {chunk["chunk_index"]: chunk for chunk in report["chunks"]}
        suspect = by_index[4]
        assert suspect["quality"]["severity"] == "high"
        assert "density_far_below_course_baseline" in suspect["quality"]["reasons"]
        assert "recognized_timeline_span_low" in suspect["quality"]["reasons"]
        assert suspect["metrics"]["relative_density"] < 0.25
        assert report["paid_provider_calls"] == 0

    def test_verified_empty_silence_is_never_a_paid_rerun_signal(self):
        job = self._job()
        for index in range(3):
            make_chunk(job, index, word_count=100)
        make_chunk(job, 3, status="EMPTY_SILENCE", word_count=0)

        report = analyze_job(job)
        silence = next(chunk for chunk in report["chunks"] if chunk["chunk_index"] == 3)
        assert silence["quality"]["severity"] == "normal"
        assert silence["quality"]["suspicious"] is False
        assert silence["quality"]["reasons"] == []

    def test_density_is_duration_normalized(self):
        job = self._job()
        # Same speaking density at different chunk lengths should stay close.
        make_chunk(job, 0, duration_ms=900_000, word_count=120)
        make_chunk(job, 1, duration_ms=450_000, word_count=60)
        make_chunk(job, 2, duration_ms=900_000, word_count=120)

        report = analyze_job(job)
        by_index = {chunk["chunk_index"]: chunk for chunk in report["chunks"]}
        ratio = by_index[1]["metrics"]["relative_density"]
        assert ratio is not None
        assert 0.75 <= ratio <= 1.25
        assert by_index[1]["quality"]["severity"] in {"normal", "low"}

    def test_missing_expected_words_artifact_is_high_severity(self):
        job = self._job()
        for index in range(3):
            make_chunk(job, index, word_count=100)
        make_chunk(job, 3, word_count=100, missing_words=True)

        report = analyze_job(job)
        item = next(chunk for chunk in report["chunks"] if chunk["chunk_index"] == 3)
        assert item["quality"]["severity"] == "high"
        assert "expected_asr_artifact_missing" in item["quality"]["reasons"]

    def test_repeated_pattern_is_reported(self):
        job = self._job()
        for index in range(3):
            make_chunk(job, index, word_count=90)
        make_chunk(job, 3, word_count=90, repeated=True)

        report = analyze_job(job)
        item = next(chunk for chunk in report["chunks"] if chunk["chunk_index"] == 3)
        assert item["metrics"]["repeat_trigram_ratio"] > 0.45
        assert "high_repeated_word_pattern" in item["quality"]["reasons"]
        assert item["quality"]["suspicious"] is True

    def test_write_report_is_derived_and_repeatable(self):
        job = self._job()
        for index in range(3):
            make_chunk(job, index, word_count=80)

        first = write_report(job)
        second = write_report(job)
        stored = json.loads((job / "asr-quality.json").read_text(encoding="utf-8"))
        assert first == second == stored
        assert stored["schema_version"] == "asr-quality-v1"
        assert stored["paid_provider_calls"] == 0


if __name__ == "__main__":
    unittest.main()
