"""Provider-free, conservative comparison of Gemini main text and Chirp words.

No linguistic majority vote and no inferred subtitle timestamps. Produces
review candidates with *existing Chirp evidence ranges* for a later audio
playback / ChatGPT-WebCodex adjudication stage.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import unicodedata
from typing import Any


class AlignmentNeedsReview(RuntimeError):
    pass


def _letters(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(c.casefold() for c in normalized if unicodedata.category(c)[0] in {"L", "N"})


def compare_gemini_chirp(
    gemini_text: str,
    chirp_words: list[dict[str, Any]],
    *,
    max_characters: int = 12000,
) -> dict[str, Any]:
    """Return differences without changing Gemini text or Chirp timeline.

    Input Chirp words must have `word`, `start_ms`, `end_ms`.
    Long recordings must compare ordered bounded windows/paragraphs instead of
    loading a huge entire-course character diff into SequenceMatcher.
    """
    if not gemini_text.strip() or not chirp_words:
        raise AlignmentNeedsReview("empty Gemini text or Chirp word evidence")
    source_chars: list[str] = []
    word_by_char: list[int] = []
    previous_end = -1
    for i, item in enumerate(chirp_words):
        start = int(item["start_ms"])
        end = int(item["end_ms"])
        if start < 0 or end <= start or start < previous_end:
            raise AlignmentNeedsReview("invalid or nonmonotonic Chirp timing")
        previous_end = end
        text = _letters(str(item.get("word", "")))
        for ch in text:
            source_chars.append(ch)
            word_by_char.append(i)
    asr_text = "".join(source_chars)
    gemini_normalized = _letters(gemini_text)
    if not asr_text or not gemini_normalized:
        raise AlignmentNeedsReview("no comparable spoken characters")
    if max(len(asr_text), len(gemini_normalized)) > max_characters:
        raise AlignmentNeedsReview("comparison too long; divide using acoustic anchors")
    matcher = SequenceMatcher(None, asr_text, gemini_normalized, autojunk=False)
    candidates = []
    for tag, start_a, end_a, start_b, end_b in matcher.get_opcodes():
        if tag == "equal":
            continue
        relevant_word_indices = sorted({word_by_char[j] for j in range(start_a, end_a)})
        if tag == "insert":  # Gemini has characters absent from independent Chirp
            kind = "GEMINI_UNSUPPORTED_ADDITION"
        elif tag == "delete":  # Chirp has speech that Gemini omitted
            kind = "GEMINI_POSSIBLE_OMISSION"
        else:
            kind = "TEXT_DISAGREEMENT"
        times = None
        if relevant_word_indices:
            times = {
                "chirp_start_ms": chirp_words[relevant_word_indices[0]]["start_ms"],
                "chirp_end_ms": chirp_words[relevant_word_indices[-1]]["end_ms"],
            }
        # Insertions cannot acquire time simply by proximity. Return only
        # adjacent real Chirp words as listening context, not a cue timestamp.
        left_word = word_by_char[start_a - 1] if start_a > 0 else None
        right_word = word_by_char[start_a] if start_a < len(word_by_char) else None
        context = {
            "left_chirp_word_ms": chirp_words[left_word]["end_ms"] if left_word is not None else None,
            "right_chirp_word_ms": chirp_words[right_word]["start_ms"] if right_word is not None else None,
        }
        candidates.append({
            "kind": kind, "comparison_status": "REVIEW_REQUIRED",
            "chirp_char_range": [start_a, end_a],
            "gemini_char_range": [start_b, end_b],
            "chirp_normalized_excerpt": asr_text[start_a:end_a],
            "gemini_normalized_excerpt": gemini_normalized[start_b:end_b],
            "chirp_timing_evidence": times,
            "adjacent_chirp_context": context,
            "gemini_timing_evidence": None,
            "auto_replacement_allowed": False,
        })
    return {
        "kind": "golden_v2_primary_chirp_comparison",
        "acoustic_coverage": "NOT_DETERMINED_BY_TEXT_DIFF",
        "exact_normalized_match": not candidates,
        "similarity_ratio_not_accuracy": matcher.ratio(),
        "pending_review_count": len(candidates),
        "candidates": candidates,
        "chirp_timing_authority": True,
        "auto_publish_authorized": False,
    }
