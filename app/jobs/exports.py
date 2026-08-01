"""Requested user-facing export formats, separate from internal evidence."""
from __future__ import annotations

from collections.abc import Iterable


DEFAULT_OUTPUT_FORMATS = ("srt", "txt", "csv")
ALLOWED_OUTPUT_FORMATS = frozenset(
    {"srt", "txt", "csv", "vtt", "ass", "docx", "pdf"}
)


def normalize_output_formats(formats: Iterable[object] | None) -> list[str]:
    """Validate and de-duplicate the user-visible export selection."""
    if formats is None:
        return list(DEFAULT_OUTPUT_FORMATS)
    normalized: list[str] = []
    for value in formats:
        if not isinstance(value, str) or value not in ALLOWED_OUTPUT_FORMATS:
            raise ValueError("Unsupported output format")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("At least one output format is required")
    return normalized
