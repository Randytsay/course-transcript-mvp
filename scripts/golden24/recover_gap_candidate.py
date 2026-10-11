"""Stage-only recovery of missing subtitle cues from preserved Chirp display segments.

Produces an auditable candidate, NEVER a formal release or Google Drive change.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from scripts.golden24.pilot_preflight import parse_srt, sha256
from scripts.golden24.chirp_word_coverage import check_chirp_coverage

def timestamp(ms: int) -> str:
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

def create_candidate(snapshot: Path, jobdir: Path, output: Path) -> dict:
    before, errors = parse_srt(snapshot / "20260927.srt")
    if errors:
        raise ValueError(f"Cannot stage from malformed SRT: {errors[:3]}")
    source = json.loads((jobdir / "source-manifest.json").read_text())
    audio_hash = sha256(snapshot / "20260927.MP3")
    if source.get("source_sha256") != audio_hash:
        raise ValueError("Historic Chirp source is NOT the backed up MP3")
    record = json.loads((jobdir / "transcript.json").read_text())
    if not record.get("source_timing", "").startswith("chirp_3"):
        raise ValueError("Corrected segments do not cite Chirp 3 timing")
    segments = record.get("display_segments", [])
    windows = [(a, b) for a,b in zip(before, before[1:])
               if b["start_ms"] - a["end_ms"] >= 60000]
    if len(windows) != 1:
        raise ValueError("Expected exactly one bounded >60s gap; inspect changes")
    prev, nxt = windows[0]
    st, en = prev["end_ms"], nxt["start_ms"]
    whole = [x for x in segments if
             int(x["start_ms"]) >= st and int(x["end_ms"]) <= en and x.get("text", "").strip()]
    crossing = [x for x in segments if int(x["start_ms"]) < en and int(x["end_ms"]) > st
                and x not in whole]
    if not whole or not crossing:
        raise ValueError("Expected recovered segments and crossing review evidence")
    added = []
    for x in whole:
        added.append({"start_ms": int(x["start_ms"]), "end_ms": int(x["end_ms"]),
                      "text": x["text"].strip()})
    for a,b in zip(added,added[1:]):
        if b["start_ms"] < a["end_ms"]:
            raise ValueError("Chirp recovery segments overlap; must review manually")
    merge = [x.copy() for x in before] + added
    merge.sort(key=lambda r: (r["start_ms"],r["end_ms"]))
    rendered = "".join(f"{i}\n{timestamp(c['start_ms'])} --> {timestamp(c['end_ms'])}\n"
                       f"{c['text']}\n\n" for i,c in enumerate(merge,1))
    output.mkdir(parents=True,exist_ok=True)
    dest = output / "20260927.REVIEW-CANDIDATE.srt"
    if dest.exists() and dest.read_text(encoding="utf-8") != rendered:
        raise ValueError("Existing candidate content differs: no silent overwrite")
    dest.write_text(rendered,encoding="utf-8")
    after, structural_errors = parse_srt(dest)
    if structural_errors:
        raise ValueError(f"Bad recovered SRT: {structural_errors[:5]}")
    old_cov = check_chirp_coverage(snapshot,jobdir,before,audio_hash)
    new_cov = check_chirp_coverage(snapshot,jobdir,after,audio_hash)
    summary={
        "schema_version":"golden24-gap-stage-v1",
        "date":"20260927",
        "status":"REVIEW",
        "publication_allowed":False,
        "source_mp3_sha256":audio_hash,
        "original_srt_sha256":sha256(snapshot / "20260927.srt"),
        "candidate_srt_sha256":sha256(dest),
        "gap_start_ms":st,"gap_end_ms":en,
        "inserted_chirp_timed_cues":len(added),
        "before_cues":len(before),"candidate_cues":len(after),
        "original_uncovered_chirp_words":old_cov["uncovered_chirp_words"],
        "candidate_uncovered_chirp_words":new_cov["uncovered_chirp_words"],
        "crossing_segments_pending_review":[{"start_ms":x["start_ms"],
                                              "end_ms":x["end_ms"],
                                              "text":x.get("text","")} for x in crossing],
        "synchronized_txt_status":"NOT_YET_GENERATED",
        "acoustic_gate_status":"NOT_YET_PASSED",
        "drive_mutations":0
    }
    (output/"20260927.gap-stage-manifest.json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshot",required=True,type=Path)
    p.add_argument("--chirp-job",required=True,type=Path)
    p.add_argument("--out-dir",required=True,type=Path)
    a=p.parse_args()
    summary=create_candidate(a.snapshot,a.chirp_job,a.out_dir)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
