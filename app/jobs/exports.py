"""Requested user-facing export formats, separate from internal evidence."""
from __future__ import annotations

from collections.abc import Iterable

DEFAULT_OUTPUT_FORMATS = ("srt", "txt")
ALLOWED_OUTPUT_FORMATS = frozenset({"srt", "txt", "csv", "vtt", "ass"})
DEPRECATED_OUTPUT_FORMATS = frozenset({"docx", "pdf"})
LEGACY_DEFAULT_OUTPUT_FORMATS = ("srt", "txt", "csv")


def normalize_output_formats(formats: Iterable[object] | None) -> list[str]:
    """Validate and de-duplicate the user-visible export selection.

    The previous UI sent ``srt + txt + csv`` as an implicit default. Treat that
    exact legacy selection as the new ``srt + txt`` default. DOCX/PDF values
    from an older browser bundle are ignored rather than breaking the job; the
    pipeline no longer generates those formats.
    """
    if formats is None:
        return list(DEFAULT_OUTPUT_FORMATS)
    values = list(formats)
    if tuple(values) == LEGACY_DEFAULT_OUTPUT_FORMATS:
        return list(DEFAULT_OUTPUT_FORMATS)
    normalized: list[str] = []
    for value in values:
        if value in DEPRECATED_OUTPUT_FORMATS:
            continue
        if not isinstance(value, str) or value not in ALLOWED_OUTPUT_FORMATS:
            raise ValueError("Unsupported output format")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("At least one output format is required")
    return normalized
