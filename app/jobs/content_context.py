"""Immutable, per-job content context used by text-only correction.

The context is deliberately stored with the job rather than inferred from a
global environment variable.  That prevents a specialised course dictionary
from silently contaminating unrelated recordings.
"""
from __future__ import annotations

import hashlib
from typing import Final

GENERAL: Final = "general"
DACHENG_BUDDHIST: Final = "dacheng_buddhist"
LEGACY_UNSPECIFIED: Final = "legacy_unspecified"
CONTENT_MODES: Final = {GENERAL, DACHENG_BUDDHIST}
MAX_DOCUMENT_CONTEXT_CHARS: Final = 2400
CONTEXT_VERSION: Final = "job-context-v1"


def normalize_content_mode(value: str | None) -> str:
    mode = str(value or GENERAL).strip().lower()
    if mode not in CONTENT_MODES:
        raise ValueError("content_mode 必須為 general 或 dacheng_buddhist")
    return mode


def normalize_document_context(value: str | None) -> str:
    context = str(value or "").strip()
    if len(context) > MAX_DOCUMENT_CONTEXT_CHARS:
        raise ValueError(f"document_context 不可超過 {MAX_DOCUMENT_CONTEXT_CHARS} 字元")
    return context


def context_digest(*, mode: str, document_context: str) -> str:
    payload = f"{CONTEXT_VERSION}\n{mode}\n{document_context}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
