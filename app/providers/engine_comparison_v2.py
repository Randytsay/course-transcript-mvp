"""Evidence-only comparison of Gemini-primary vs independent Chirp words.

Differences are review candidates, NEVER automatically accepted edits. The
reported audio window comes exclusively from Chirp words, not LLM timestamps.
"""
from __future__ import annotations

import difflib
import unicodedata
from typing import Any

_DROP = frozenset("，。、！？；：,.!?;:…—\n\t \r「」『』（）()《》\u3000")


def _normalized_with_word_positions(words: list[dict[str, Any]]) -> tuple[str, list[int]]:
    chars: list[str] = []
    owners: list[int] = []
    previous_end = -1
    for i, item in enumerate(words):
        start, end = int(item["start_ms"]), int(item["end_ms"])
        if start < 0 or end <= start or start < previous_end:
            raise ValueError("Chirp source words must have monotonic valid timing")
        previous_end = end
        for char in unicodedata.normalize("NFKC", str(item.get("word") or item.get("text") or "")):
            if char in _DROP or char.isspace():
                continue
            chars.append(char.casefold())
            owners.append(i)
    return "".join(chars), owners


def _text_only(text: str) -> str:
    return "".join(
        char.casefold()
        for char in unicodedata.normalize("NFKC", text)
        if char not in _DROP and not char.isspace()
    )


def compare_primary_to_chirp(
    *,
    primary_text: str,
    chirp_words: list[dict[str, Any]],
    transcribe_text: str | None = None,
) -> dict[str, Any]:
    """List verbatim differences with acoustic evidence and review status.

    An inserted Gemini phrase (no matching Chirp word) is anchored to nearby
    Chirp timing as a **review listening window**, not an accepted subtitle cue.
    """
    chirp_text, owners = _normalized_with_word_positions(chirp_words)
    primary = _text_only(primary_text)
    third = _text_only(transcribe_text) if transcribe_text is not None else None
    if not chirp_text or not primary:
        return {
            "status": "NEEDS_REVIEW", "reason": "empty_primary_or_chirp",
            "issues": [], "issue_count": 0,
            "timing_source": "chirp_3", "auto_apply": False,
        }
    differences: list[dict[str, Any]] = []
    ops = difflib.SequenceMatcher(None, chirp_text, primary, autojunk=False).get_opcodes()
    for tag, a0, a1, b0, b1 in ops:
        if tag == "equal":
            continue
        if a0 < a1:
            first, last = owners[a0], owners[a1 - 1]
        else:
            # No direct Chirp match. Neighboring words bound a *listening*
            # window; these boundaries are not suitable as subtitle timing.
            first = owners[a0 - 1] if a0 else 0
            last = owners[a0] if a0 < len(owners) else len(chirp_words) - 1
        source_slice = chirp_text[a0:a1]
        primary_slice = primary[b0:b1]
        label = {
            "delete": "GEMINI_POSSIBLE_OMISSION",
            "insert": "GEMINI_POSSIBLE_ADDITION",
            "replace": "WORDING_CONFLICT",
        }[tag]
        differences.append({
            "type": label,
            "chirp_text": source_slice,
            "gemini_text": primary_slice,
            "source_start_ms": int(chirp_words[first]["start_ms"]),
            "source_end_ms": int(chirp_words[last]["end_ms"]),
            "timestamp_kind": "chirp_context_window" if a0 == a1 else "chirp_word_span",
            "transcribe_supports_gemini": bool(primary_slice and third is not None and primary_slice in third),
            "transcribe_supports_chirp": bool(source_slice and third is not None and source_slice in third),
            "adjudication": "NEEDS_AUDIO_REVIEW",
        })
    return {
        "status": "MATCH" if not differences else "NEEDS_REVIEW",
        "issues": differences,
        "issue_count": len(differences),
        "timing_source": "chirp_3",
        "auto_apply": False,
        "transcribe_present": third is not None,
    }
