"""Deterministic Golden V2 SRT/TXT from Chirp word boundaries.

Corrections spanning multiple words own the original entire source span. No
character-level timestamp interpolation or ungrounded new words are allowed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict
from app.subtitles.editor_hardened import parse_srt_strict

_PUNCTUATION = frozenset("，。！？；：、,.!?;:")


def _stamp(ms: int) -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, sub = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{sub:03d}"


def _word_evidence(word: dict[str, Any], index: int, source_sha: str) -> str:
    payload = [source_sha, index, word["word"], word["start_ms"], word["end_ms"]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def render_golden_v2(
    *, merged: dict[str, Any], replacements: list[dict[str, Any]] | None = None,
    punctuation_after: dict[int, str] | None = None,
    max_characters: int = 20, max_cue_ms: int = 6500,
) -> dict[str, Any]:
    if not 4 <= max_characters <= 80 or not 500 <= max_cue_ms <= 20_000:
        raise V2Conflict("invalid deterministic cue layout limits")
    words = merged.get("words")
    source_sha = merged.get("source_sha")
    if (not isinstance(words, list) or not words
            or merged.get("timing_authority") != "chirp_3"
            or not isinstance(source_sha, str) or len(source_sha) != 64
            or any(c not in "0123456789abcdef" for c in source_sha)):
        raise V2Conflict("immutable Chirp source words required for rendering")
    last = 0
    for word in words:
        if (not isinstance(word, dict) or type(word.get("start_ms")) is not int
                or type(word.get("end_ms")) is not int or not str(word.get("word") or "").strip()
                or word["start_ms"] < last or word["end_ms"] <= word["start_ms"]):
            raise V2Conflict("invalid overlapping Chirp word evidence")
        last = word["end_ms"]
    patches: dict[int, dict[str, Any]] = {}
    occupied = set()
    for replacement in replacements or []:
        if not isinstance(replacement, dict):
            raise V2Conflict("correction must be a source-bound object")
        start, end = replacement.get("start_word"), replacement.get("end_word")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(words)
                or not isinstance(replacement.get("corrected_text"), str)
                or not replacement["corrected_text"].strip()
                or not replacement.get("audio_evidence_checked")
                or replacement.get("source_sha256") != source_sha
                or not isinstance(replacement.get("chirp_word_evidence_shas"), list)
                or sorted(replacement["chirp_word_evidence_shas"]) != sorted(
                    _word_evidence(words[i], i, source_sha) for i in range(start, end))
                or not str(replacement.get("decision_reason") or "").strip()
                or any(i in occupied for i in range(start, end))):
            raise V2Conflict("word correction lacks unique audio/source-span evidence")
        patches[start] = replacement
        occupied.update(range(start, end))

    punctuation_after = punctuation_after or {}
    if (not isinstance(punctuation_after, dict)
            or any(type(key) is not int or not 0 <= key < len(words)
                   or not isinstance(value, str)
                   or any(character not in _PUNCTUATION for character in value)
                   or len(value) > 3 for key, value in punctuation_after.items())):
        raise V2Conflict("unsupported punctuation boundary")

    tokens: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(words):
        patch = patches.get(cursor)
        end_index = patch["end_word"] if patch else cursor + 1
        if any(cursor <= i < end_index - 1 for i in punctuation_after):
            raise V2Conflict("punctuation cannot split a corrected source span")
        text = patch["corrected_text"] if patch else words[cursor]["word"]
        text += punctuation_after.get(end_index - 1, "")
        tokens.append({"start_ms": words[cursor]["start_ms"],
                       "end_ms": words[end_index - 1]["end_ms"],
                       "text": text, "start_word": cursor, "end_word": end_index})
        cursor = end_index

    cues: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if current:
            cues.append({"start_ms": current[0]["start_ms"],
                         "end_ms": current[-1]["end_ms"],
                         "text": "".join(token["text"] for token in current),
                         "first_word": current[0]["start_word"],
                         "last_word_exclusive": current[-1]["end_word"]})
            current.clear()

    for token in tokens:
        if (len(token["text"]) > max_characters
                or token["end_ms"] - token["start_ms"] > max_cue_ms):
            raise V2Conflict("single immutable Chirp span exceeds cue limits; review required")
        if current and (len("".join(t["text"] for t in current)) + len(token["text"]) > max_characters
                        or token["end_ms"] - current[0]["start_ms"] > max_cue_ms
                        or token["start_ms"] - current[-1]["end_ms"] > 900):
            flush()
        current.append(token)
        if token["text"].endswith(("。", "！", "？", ".", "!", "?")):
            flush()
    flush()
    srt = "\n\n".join(f"{i}\n{_stamp(c['start_ms'])} --> {_stamp(c['end_ms'])}\n{c['text']}"
                     for i, c in enumerate(cues, 1)) + "\n"
    transcript = "\n".join(c["text"] for c in cues) + "\n"
    parsed, _ = parse_srt_strict(srt)
    if (len(parsed) != len(cues) or any(not c["text"].strip() for c in cues)):
        raise V2Conflict("deterministic SRT failed strict structural validation")
    return {"srt": srt, "transcript": transcript,
            "cue_manifest": cues, "timing_authority": "chirp_3",
            "source_sha256": source_sha,
            "replacements_applied": len(patches),
            "srt_sha256": hashlib.sha256(srt.encode()).hexdigest(),
            "transcript_sha256": hashlib.sha256(transcript.encode()).hexdigest()}
