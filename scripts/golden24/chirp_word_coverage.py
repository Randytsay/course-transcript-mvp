"""Independent word-timestamp coverage check against final SRT cue time spans."""
from __future__ import annotations

import json
from pathlib import Path

def check_chirp_coverage(snapshot: Path, job_dir: Path, cues: list[dict], expected_sha: str) -> dict:
    src = json.loads((job_dir / "source-manifest.json").read_text(encoding="utf-8"))
    word_record = json.loads((job_dir / "merged-words.json").read_text(encoding="utf-8"))
    if src.get("source_sha256") != expected_sha or src.get("source_name") != "20260927.MP3":
        raise ValueError("Chirp source provenance does not match the backed-up original MP3")
    words = word_record.get("words", [])
    if len(words) != word_record.get("total_words"):
        raise ValueError("Chirp merged-words count mismatch")
    if not words or not cues:
        raise ValueError("Missing source words or subtitle cues")
    ci = 0
    un = []
    for word in words:
        start, end = int(word["start_ms"]), int(word["end_ms"])
        midpoint = (start + end) // 2
        while ci < len(cues) and cues[ci]["end_ms"] < midpoint:
            ci += 1
        if ci >= len(cues) or cues[ci]["start_ms"] > midpoint:
            un.append(word)
    runs = []
    for w in un:
        start, end = int(w["start_ms"]), int(w["end_ms"])
        if runs and start - runs[-1]["end_ms"] <= 1500:
            q = runs[-1]
            q["end_ms"] = max(q["end_ms"], end)
            q["word_count"] += 1
            if len(q["excerpt"]) < 150:
                q["excerpt"] += w["word"]
        else:
            runs.append({"start_ms": start, "end_ms": end,
                         "word_count": 1, "excerpt": w["word"]})
    for r in runs:
        r["duration_ms"] = r["end_ms"] - r["start_ms"]
    significant = [r for r in runs if r["word_count"] >= 10 and r["duration_ms"] >= 5000]
    significant.sort(key=lambda r: (-r["word_count"], -r["duration_ms"]))
    return {
        "bound_source_name": src["source_name"],
        "verified_original_sha256": expected_sha,
        "total_chirp_words": len(words),
        "uncovered_chirp_words": len(un),
        "significant_uncovered_runs": len(significant),
        "significant_uncovered_top20": significant[:20],
        "requires_acoustic_review": bool(significant),
        "caveat": "Chirp timestamps evidence potential speech; ASR errors and nonlexical audio still require QA",
    }
