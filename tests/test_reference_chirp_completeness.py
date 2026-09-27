import json
from pathlib import Path

from app.providers import reference_chirp_completeness as gate


def _words(text: str):
    out = []
    t = 0
    for ch in text:
        out.append({"word": ch, "start_ms": t, "end_ms": t + 200})
        t += 200
    return out


def test_evaluate_requires_material_improvement():
    candidate = {"reference_normalized": "上報四重恩下濟三途苦"}
    original = _words("上報四恩下濟苦")
    improved = _words("上報四重恩下濟三途苦")
    result = gate._evaluate(candidate, original, improved)
    assert result["improved"] is True
    assert result["rerun_ratio"] > result["original_ratio"]


def test_evaluate_rejects_no_improvement():
    candidate = {"reference_normalized": "佛說彌勒大成佛經"}
    original = _words("佛說彌勒大成佛經")
    rerun = _words("佛說彌勒大成佛經")
    result = gate._evaluate(candidate, original, rerun)
    assert result["improved"] is False


def test_splice_words_changes_only_selected_range():
    original = [
        {"word": "甲", "start_ms": 0, "end_ms": 100},
        {"word": "錯", "start_ms": 100, "end_ms": 200},
        {"word": "誤", "start_ms": 200, "end_ms": 300},
        {"word": "乙", "start_ms": 300, "end_ms": 400},
    ]
    candidate = {"word_start": 1, "word_end": 2}
    replacement = [{"word": "正確", "start_ms": 100, "end_ms": 300}]
    patched = gate._splice_words(original, [(candidate, replacement)])
    assert [x["word"] for x in patched] == ["甲", "正確", "乙"]
    assert patched[0] == original[0]
    assert patched[-1] == original[-1]


def test_main_skips_without_reference(tmp_path, monkeypatch):
    job = tmp_path / "jobs" / "j1"
    job.mkdir(parents=True)
    monkeypatch.setattr(gate, "JOB", job)
    assert gate.main() == 0
    report = json.loads((job / gate.REPORT).read_text())
    assert report["status"] == "SKIPPED"
    assert report["provider_calls"] == 0


def test_candidate_detector_matches_real_20260913_when_fixture_available():
    root = Path("/opt/course-transcript-source/data/jobs/20260913-20260926-070020-8bed74")
    ref = root / "chatgpt-handoff/reference-transcript.txt"
    merged = root / "merged-words.json"
    if not ref.exists() or not merged.exists():
        return
    candidates, summary = gate.detect_candidates(
        ref.read_text(), json.loads(merged.read_text()), max_candidates=5
    )
    paragraphs = [item["paragraph_order"] for item in candidates]
    assert paragraphs == [29, 44, 50, 52, 75]
    assert summary["candidate_count"] == 5


def test_cost_cap_blocks_provider_calls(tmp_path, monkeypatch):
    job = tmp_path / "jobs" / "j2"
    job.mkdir(parents=True)
    (job / "reference-transcript.txt").write_text("這是一份足夠長的人工逐字稿內容。")
    (job / "normalized.flac").write_bytes(b"x")
    (job / "merged-words.json").write_text(
        json.dumps({"words": [{"word": "測", "start_ms": 0, "end_ms": 1000}]}),
        encoding="utf-8",
    )
    candidate = {
        "paragraph_order": 1,
        "reason": "same_chirp_word_semantic_collapse_density",
        "coverage": 0.8,
        "word_start": 0,
        "word_end": 0,
        "core_start_ms": 0,
        "core_end_ms": 1_800_000,
        "duration_ms": 1_800_000,
        "reference_text": "測試",
        "reference_normalized": "測試",
    }
    monkeypatch.setattr(gate, "JOB", job)
    monkeypatch.setattr(gate, "detect_candidates", lambda *a, **k: ([candidate], {"candidate_count": 1}))
    called = {"n": 0}

    def no_provider(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("provider must not be called above cost cap")

    monkeypatch.setattr(gate, "_recognize", no_provider)
    monkeypatch.setenv("REFERENCE_CHIRP_AUTO_MAX_USD", "0.01")
    assert gate.main() == 0
    report = json.loads((job / gate.REPORT).read_text())
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["reason"] == "automatic_cost_cap_exceeded"
    assert report["provider_calls"] == 0
    assert called["n"] == 0
