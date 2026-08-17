"""Collaborative subtitle review domain.

This package is intentionally separate from the paid transcription pipeline.
Reviewer identities, YouTube video/caption metadata, progress, suggestions, and
contribution history share the existing SQLite database without changing Chirp
or Gemini evidence.
"""

from .store import ReviewConflict, ReviewNotFound, ReviewStore, changed_char_count

__all__ = [
    "ReviewConflict",
    "ReviewNotFound",
    "ReviewStore",
    "changed_char_count",
]
