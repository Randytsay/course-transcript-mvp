"""Provider-free P5 review-only candidate from immutable four-minute evidence.

This module NEVER calls Google APIs, modifies Drive, adjusts cost ledgers, or
marks Golden PASS. Human segment preference is NOT a word-by-word transcript.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

MODEL_PRIMARY = "gemini-3.8-flash"
MODEL_TIMING = "chirp_3"
MODEL_THIRD = "gemini-3.5-transcribe-preview"
_DURATION = 240.0
_STAMP = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")
_CHOICE = frozenset({"both_ok", "gemini", "chirp", "both_wrong", "not_sure"})


class GoldenP5EvidenceError(ValueError):
    """Evidence is missing, stale, contradictory or unauthorized."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def normalized(text: str) -> str:
    return "".join(char.casefold() for char in text if char.isalnum())


def timestamp(stamp: str) -> float:
    match = _STAMP.fullmatch(stamp)
    if not match:
        raise GoldenP5EvidenceError("invalid SRT timestamp")
    h, m, s, ms = map(int, match.groups())
    if m >= 60 or s >= 60:
        raise GoldenP5EvidenceError("out-of-range SRT timestamp")
    return h * 3600 + m * 60 + s + ms / 1000


def read_cues(srt: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", srt.strip())
    cues: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(index):
            raise GoldenP5EvidenceError("nonsequential or empty SRT cue")
        if lines[1].count(" --> ") != 1:
            raise GoldenP5EvidenceError("invalid SRT cue span")
        start_str, end_str = lines[1].split(" --> ")
        start, end = timestamp(start_str), timestamp(end_str)
        text = "\n".join(lines[2:]).strip()
        if not text or not 0 <= start < end <= _DURATION + .001:
            raise GoldenP5EvidenceError("invalid or empty SRT time/text")
        if cues and start < cues[-1]["end"] - .001:
            raise GoldenP5EvidenceError("overlapping SRT cue times")
        cues.append({"index": index, "start": start, "end": end, "text": text})
    if not cues:
        raise GoldenP5EvidenceError("SRT has no cues")
    return cues


def validate_payload(
    *, audio: bytes, primary_raw: bytes, chirp_raw: bytes, third_raw: bytes,
    review_raw: bytes, reviewed_srt: str, chirp_srt: str,
) -> dict[str, Any]:
    expected_sha = sha256(audio)
    if len(audio) < 100_000:
        raise GoldenP5EvidenceError("four-minute FLAC absent/too small")
    items = [json.loads(x) for x in (primary_raw, chirp_raw, third_raw, review_raw)]
    primary, chirp, third, review = items
    for name, value, expected_model in [
        ("primary", primary, MODEL_PRIMARY), ("chirp", chirp, MODEL_TIMING),
        ("third", third, MODEL_THIRD),
    ]:
        if (value.get("source_sha256") != expected_sha
                or value.get("model") != expected_model
                or value.get("status") != "COMPLETE"):
            raise GoldenP5EvidenceError(name + ": wrong source, model or status")
    if review.get("clip_sha256") != expected_sha:
        raise GoldenP5EvidenceError("owner review source SHA mismatch")
    if (review.get("source_start_seconds") != 600
            or review.get("reviewed_sections") != 12
            or review.get("total_sections") != 12
            or review.get("unreviewed_sections") != []
            or review.get("srt_status") != "DRAFT_NEEDS_SEGMENT_ALIGNMENT_AND_ACCEPTANCE"):
        raise GoldenP5EvidenceError("owner review not complete or not marked draft")
    choices = review.get("choices")
    if not isinstance(choices, dict) or set(choices) != {str(i) for i in range(1, 13)}:
        raise GoldenP5EvidenceError("review must contain exactly 12 segment choices")
    if any(choice not in _CHOICE for choice in choices.values()):
        raise GoldenP5EvidenceError("unsupported owner segment choice")

    text_primary = str(primary.get("model_text") or "").strip()
    text_chirp = str(chirp.get("transcript") or "").strip()
    text_third = str(third.get("transcript") or "").strip()
    if min(map(len, (text_primary, text_chirp, text_third))) < 50:
        raise GoldenP5EvidenceError("model transcript is empty or truncated")
    words = chirp.get("words")
    if (not chirp.get("word_time_offsets_verified")
            or not isinstance(words, list) or not words
            or chirp.get("billed_duration_seconds") != 240):
        raise GoldenP5EvidenceError("missing Chirp acoustic/timing evidence")
    for word in words:
        start, end = word.get("start_seconds"), word.get("end_seconds")
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not 0 <= start < end <= _DURATION + .001):
            raise GoldenP5EvidenceError("invalid Chirp word timestamp")
    if third.get("chirp_remains_time_authority") is not True:
        raise GoldenP5EvidenceError("third model may not override Chirp timing")

    prior = third.get("previous_receipts")
    expected_proofs = {
        MODEL_PRIMARY: sha256(primary_raw), MODEL_TIMING: sha256(chirp_raw),
    }
    if (not isinstance(prior, list) or len(prior) != 2
            or {p.get("model"): p.get("receipt_sha256") for p in prior
                if isinstance(p, dict)} != expected_proofs):
        raise GoldenP5EvidenceError("third receipt lacks source-bound previous evidence")

    cues = read_cues(reviewed_srt)
    raw_cues = read_cues(chirp_srt)
    corrections = review.get("human_confirmed")
    if not isinstance(corrections, list) or not corrections:
        raise GoldenP5EvidenceError("missing explicit owner corrections")
    accepted: list[dict[str, Any]] = []
    for correction in corrections:
        if (not isinstance(correction, dict)
                or correction.get("status") != "ACCEPTED_USER_WORDING"
                or not isinstance(correction.get("correct_phrase"), str)
                or not correction["correct_phrase"].strip()
                or not isinstance(correction.get("previous_chirp_phrase"), str)
                or not correction["previous_chirp_phrase"].strip()):
            raise GoldenP5EvidenceError("invalid confirmed correction")
        match = [c for c in cues if correction["correct_phrase"] in c["text"]]
        if len(match) != 1:
            raise GoldenP5EvidenceError("explicit correction not uniquely in timed SRT")
        if correction["previous_chirp_phrase"] in reviewed_srt:
            raise GoldenP5EvidenceError("old incorrect phrase remains in SRT")
        accepted.append({
            "section": correction["section"], "cue": match[0]["index"],
            "correct_text": correction["correct_phrase"],
            "source": "user_explicit_wording_attestation",
            "word_level_full_audio_approval": False,
        })
    # Fail closed if an unapproved lexical edit was inserted in the timed SRT.
    if len(raw_cues) != len(cues):
        raise GoldenP5EvidenceError("review changed cue count without word-alignment evidence")
    allowed_spans = {
        item["cue"]: next(c for c in corrections if c["correct_phrase"] == item["correct_text"])
        for item in accepted
    }
    for previous, current in zip(raw_cues, cues):
        if (previous["index"], previous["start"], previous["end"]) != (
                current["index"], current["start"], current["end"]):
            raise GoldenP5EvidenceError("review changed acoustic cue indices or timestamps")
        if previous["text"] == current["text"]:
            continue
        evidence = allowed_spans.get(previous["index"])
        if (evidence is None
                or evidence["previous_chirp_phrase"] not in previous["text"]
                or normalized(previous["text"].replace(
                    evidence["previous_chirp_phrase"], evidence["correct_phrase"]))
                != normalized(current["text"])):
            raise GoldenP5EvidenceError("review SRT contains an unapproved lexical change")
    candidate_txt = "\n".join(c["text"] for c in cues) + "\n"
    if normalized(candidate_txt) != normalized("".join(c["text"] for c in cues)):
        raise GoldenP5EvidenceError("candidate TXT/SRT text mismatch")
    candidate_raw = reviewed_srt.rstrip() + "\n"
    score = lambda a,b: round(difflib.SequenceMatcher(None,normalized(a),normalized(b),autojunk=False).ratio(),4)
    possible_gaps = [
        {"after_cue": left["index"], "start_seconds": left["end"],
         "end_seconds": right["start"], "gap_seconds": round(right["start"]-left["end"],3),
         "classification": "UNCHECKED_NOT_EVIDENCE_OF_SPEECH"}
        for left, right in zip(cues, cues[1:])
        if right["start"]-left["end"] > 1.5
    ]
    evidence = {
        "clip_sha256": expected_sha, "audio_size_bytes": len(audio),
        "primary_raw_sha256": sha256(primary_raw),
        "chirp_raw_sha256": sha256(chirp_raw),
        "third_raw_sha256": sha256(third_raw),
        "human_review_sha256": sha256(review_raw),
        "reviewed_srt_sha256": sha256(reviewed_srt.encode()),
        "original_chirp_srt_sha256": sha256(chirp_srt.encode()),
        "time_basis": "240_second_clip_relative",
        "parent_source_offset_seconds": 600,
    }
    comparison = {
        "status": "TEXT_COMPARISON_NOT_ACCURACY",
        "model_characters": {MODEL_PRIMARY:len(text_primary), MODEL_TIMING:len(text_chirp), MODEL_THIRD:len(text_third)},
        "normalized_similarity": {
            "preview_to_gemini38":score(text_third,text_primary),
            "preview_to_chirp3":score(text_third,text_chirp),
            "gemini38_to_chirp3":score(text_primary,text_chirp),
        },
        "user_sample_preference_not_global_ranking": ["gemini-3.8-flash","chirp_3","gemini-3.5-transcribe-preview"],
        "evidence": evidence,
    }
    adjudication = {
        "status":"REVIEW_REQUIRED",
        "segmented_owner_choices": [
            {"section":i,"choice":choices[str(i)],"decision_state":"HUMAN_PREFERENCE_RECORDED_NOT_WORD_LEVEL_GOLD"}
            for i in range(1,13)
        ],
        "explicit_text_corrections": accepted,
        "candidate_timing_source":"chirp_3",
        "candidate_text_basis":"chirp_reviewed_SRT_with_explicit_owner_correction",
        "gemini38_main_text":"semantic_reference_only_until_timely_alignment",
        "third_model":"disagreement_reference_only",
        "audio_relisten_proven_by_this_program": False,
        "evidence": evidence,
    }
    blockers = [
        "NO_INDEPENDENT_FULL_AUDIO_LISTENING_PROOF",
        "NO_VAD_VS_CHIRP_GAP_COVERAGE",
        "NO_WORD_LEVEL_GEMINI38_TO_CHIRP_ALIGNMENT",
        "UNRESOLVED_ENGINE_TEXT_DIFFERENCES",
        "NO_FINAL_SEMANTIC_AND_SUBTITLE_SEGMENTATION_APPROVAL",
    ]
    qa = {
        "status":"REVIEW_REQUIRED", "golden_pass":False, "safe_publish_allowed":False,
        "source_sha_verified":True, "three_real_provider_receipts_verified":True,
        "owner_segment_preferences_recorded":12,"explicit_owner_corrections":len(accepted),
        "chirp_word_offsets_verified":len(words),"candidate_srt_cue_count":len(cues),
        "candidate_srt_txt_same_normalized_text":True,
        "possible_unreviewed_acoustic_gaps":possible_gaps,
        "note":"Time gaps do not prove missing speech; independently verify original audio/VAD before resolving.",
        "blocking_reasons":blockers,
        "prior_gemini35_ga_failures":"OUTCOME_UNKNOWN_PRESERVED_SEPARATELY",
        "evidence":evidence,
    }
    return {
        "golden-candidate-REVIEW_ONLY.srt":candidate_raw,
        "golden-candidate-REVIEW_ONLY.txt":candidate_txt,
        "engine-comparison.json":comparison,
        "adjudication.json":adjudication,
        "golden-qa-report.json":qa,
    }


def write_create_only(folder: Path, artifacts: dict[str, Any]) -> None:
    if folder.exists():
        raise GoldenP5EvidenceError("output directory exists; no overwriting evidence")
    folder.mkdir(parents=True, mode=0o700)
    for name, payload in artifacts.items():
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with (folder / name).open("x",encoding="utf-8") as writer:
            writer.write(body)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("audio", "primary", "chirp", "third", "owner_review", "reviewed_srt", "chirp_srt", "out"):
        p.add_argument("--" + name.replace("_","-"), type=Path, required=True)
    args = p.parse_args()
    result = validate_payload(
        audio=args.audio.read_bytes(),
        primary_raw=args.primary.read_bytes(),
        chirp_raw=args.chirp.read_bytes(),
        third_raw=args.third.read_bytes(),
        review_raw=args.owner_review.read_bytes(),
        reviewed_srt=args.reviewed_srt.read_text(encoding="utf-8"),
        chirp_srt=args.chirp_srt.read_text(encoding="utf-8"),
    )
    write_create_only(args.out,result)
    report = result["golden-qa-report.json"]
    print(json.dumps({
        "status":report["status"],"golden_pass":False,
        "files":sorted(result),"blockers":report["blocking_reasons"],
        "provider_calls_started":False,"drive_mutated":False,
    },ensure_ascii=False))


if __name__ == "__main__":
    main()

