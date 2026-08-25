"""Canonical current-subtitle resolution shared by every entry point.

There is exactly ONE definition of "the subtitle currently in use" for a
job:

1. If an AI Review active revision exists, its cue list is canonical.
   AI revisions are cue-aware: merged/reflowed cues carry multi-source
   lineage, so they cannot be flattened back into per-segment editor edits.

2. Otherwise the Subtitle Editor state is canonical:
   subtitles-corrected.json (Gemini corrected_text)
   + subtitle-editor.json manual edit overlay
   resolved through editor._current_segments().

SOURCE BASELINE evidence (segment_id / start_ms / end_ms / raw_text) stays
immutable under both branches; only the working representation differs.

Consumers that MUST use this module instead of resolving on their own:
- GET /api/v1/subtitles/{id} (editor read view)
- Subtitle Editor manual editing base
- AI Review candidate input validation
- AI Review Active export
- Editor Drive publish render
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_active_revision(directory: Path) -> dict[str, Any] | None:
    """Return the active AI revision record, or None when AI Review has no
    published revision yet. Kept dependency-free so the editor can call it
    without circular imports."""
    payload = _read_json(directory / "ai-subtitle-review.json")
    if not payload or payload.get("active_revision") is None:
        return None
    try:
        number = int(payload["active_revision"])
    except (TypeError, ValueError):
        return None
    for item in payload.get("revisions") or []:
        if isinstance(item, dict) and int(item.get("revision", -1)) == number:
            cues = item.get("cues")
            if isinstance(cues, list):
                return item
    return None


def canonical_cues(
    directory: Path,
    editor_segments: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Resolve the canonical current subtitle as presentation cues.

    Returns (cues, source) where source is "ai_review_active" or "editor".
    When the editor branch is taken, ``editor_segments`` may be supplied by
    callers that already resolved editor._current_segments() to avoid
    duplicate work; each item needs segment_id/start_ms/end_ms/current_text.
    """
    active = load_active_revision(directory)
    if active is not None:
        cues: list[dict[str, Any]] = []
        for cue in active["cues"]:
            cues.append(
                {
                    "text": str(cue.get("text", "")),
                    "source_segment_ids": [str(v) for v in cue.get("source_segment_ids", [])],
                    "start_ms": int(cue.get("start_ms", 0)),
                    "end_ms": int(cue.get("end_ms", 0)),
                    "cue_id": cue.get("cue_id"),
                }
            )
        return sorted(cues, key=lambda c: (c["start_ms"], c["end_ms"])), "ai_review_active"

    from app.subtitles.editor import _current_segments as editor_current

    segments = (
        editor_segments
        if editor_segments is not None
        else editor_current(directory)[0]
    )
    return (
        [
            {
                "text": str(item["current_text"]),
                "source_segment_ids": [str(item["segment_id"])],
                "start_ms": int(item["start_ms"]),
                "end_ms": int(item["end_ms"]),
                "cue_id": f"cue-{index:04d}",
            }
            for index, item in enumerate(segments, 1)
        ],
        "editor",
    )
