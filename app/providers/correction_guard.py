"""Provider-independent guard for text-only subtitle corrections."""
from __future__ import annotations

import re
from difflib import SequenceMatcher


def content_guard(raw: str, corrected: str) -> list[str]:
    """Return severe rewrite indicators before accepting provider text."""
    raw_text = "".join(raw.split())
    corrected_text = "".join(corrected.split())
    reasons: list[str] = []
    if raw_text and not corrected_text:
        return ["empty_correction"]
    if len(corrected_text) > 4_000:
        reasons.append("correction_too_long")
    if re.search(r"(.)\1{5,}", corrected_text):
        reasons.append("repeated_character_run")
    if len(raw_text) >= 8:
        ratio = len(corrected_text) / max(1, len(raw_text))
        if ratio < 0.30:
            reasons.append("excessive_deletion")
        elif ratio > 2.50:
            reasons.append("excessive_addition")
        similarity = SequenceMatcher(None, raw_text, corrected_text).ratio()
        if len(raw_text) >= 20 and similarity < 0.20:
            reasons.append("semantic_rewrite_risk")
        elif similarity < 0.15 and abs(
            len(corrected_text) - len(raw_text)
        ) > max(12, round(len(raw_text) * 0.8)):
            reasons.append("semantic_rewrite_risk")
    return reasons
