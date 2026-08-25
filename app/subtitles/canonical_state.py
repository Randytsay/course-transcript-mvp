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


def _review_payload(directory: Path) -> dict[str, Any] | None:
    path = directory / "ai-subtitle-review.json"
    if not path.exists():
        return None
    payload = _read_json(path)
    if payload is None:
        raise HTTPException(status_code=409, detail="AI Review state 無法讀取；拒絕回退到舊 Editor state")
    return payload


def load_active_revision(directory: Path) -> dict[str, Any] | None:
    """Return Active Revision; declared-but-invalid state fails closed."""
    payload = _review_payload(directory)
    if not payload or payload.get("active_revision") is None:
        return None
    value = payload.get("active_revision")
    if isinstance(value, bool):
        raise HTTPException(status_code=409, detail="AI Review active_revision 無效")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="AI Review active_revision 無效") from exc
    revisions = payload.get("revisions")
    if not isinstance(revisions, list):
        raise HTTPException(status_code=409, detail="AI Review revisions state 無效")
    for item in revisions:
        if not isinstance(item, dict):
            raise HTTPException(status_code=409, detail="AI Review revision record 無效")
        item_value = item.get("revision")
        if isinstance(item_value, bool):
            raise HTTPException(status_code=409, detail="AI Review revision number 無效")
        try:
            item_number = int(item_value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="AI Review revision number 無效") from exc
        if item_number == number:
            cues = item.get("cues")
            if not isinstance(cues, list) or not cues:
                raise HTTPException(status_code=409, detail="AI Review Active Revision 缺少有效 cues")
            return item
    raise HTTPException(status_code=409, detail="AI Review Active Revision 指向不存在的 revision")


def editor_mutation_block_reason(directory: Path) -> str | None:
    """Legacy per-segment editor cannot mutate an AI-owned/reviewing state."""
    payload = _review_payload(directory)
    if not payload:
        return None
    if load_active_revision(directory) is not None:
        return "AI Review 已有 Active Revision；舊 per-segment Editor 暫不可寫入，請到 AI Review 繼續修訂"
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise HTTPException(status_code=409, detail="AI Review candidates state 無效")
    if candidates:
        return "AI Review 尚有未發布候選；請先完成本輪 Review，再修改 Editor"
    return None


def ensure_editor_mutation_allowed(directory: Path) -> None:
    reason = editor_mutation_block_reason(directory)
    if reason:
        raise HTTPException(status_code=409, detail=reason)


def ensure_editor_mutation_allowed_for_legacy_only(directory: Path) -> None:
    """Legacy publish path guard: block only while candidates are pending.

    With a published Active AI revision, publication goes through the
    canonical publication identity (publication_key), so the old
    editor-revision-based lock no longer applies here.
    """
    payload = _review_payload(directory)
    if not payload:
        return
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise HTTPException(status_code=409, detail="AI Review candidates state 無效")
    if any(isinstance(item, dict) and item.get("status") == "pending" for item in candidates):
        raise HTTPException(
            status_code=409,
            detail="AI Review 尚有未審核候選；請先完成本輪 Review 再發布",
        )


import hashlib


def _cues_content_sha256(cues: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [
            {
                "text": str(cue.get("text", "")),
                "source_segment_ids": [str(v) for v in cue.get("source_segment_ids", [])],
                "start_ms": int(cue.get("start_ms", 0)),
                "end_ms": int(cue.get("end_ms", 0)),
            }
            for cue in cues
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def publication_identity(
    directory: Path,
    editor_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Namespaced canonical publication identity.

    An editor integer revision alone cannot identify a published version once
    an AI Review Active Revision exists (same number, different content).
    The stable key binds source namespace + that source's own revision +
    content hash, so Editor rev1 and AI R1 never collide and any content
    change produces a new key.
    """
    cues, source = canonical_cues(directory, editor_segments)
    content_sha256 = _cues_content_sha256(cues)
    if source == "ai_review_active":
        active = load_active_revision(directory)
        canonical_revision = int(active["revision"]) if active else 0
    else:
        from app.subtitles.editor import _edit_state

        canonical_revision = int(_edit_state(directory)["revision"])
    return {
        "canonical_source": source,
        "canonical_revision": canonical_revision,
        "content_sha256": content_sha256,
        # Deterministic, human-readable, collision-free across namespaces.
        "publication_key": f"{source}:r{canonical_revision}:{content_sha256[:16]}",
    }

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
        for raw_cue in active["cues"]:
            if not isinstance(raw_cue, dict):
                raise HTTPException(status_code=409, detail="AI Review Active Revision cue 無效")
            lineage = [str(v) for v in raw_cue.get("source_segment_ids", [])]
            if not lineage:
                raise HTTPException(status_code=409, detail="AI Review Active Revision cue 缺少 lineage")
            try:
                start_ms = int(raw_cue["start_ms"])
                end_ms = int(raw_cue["end_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail="AI Review Active Revision timestamp 無效") from exc
            value = str(raw_cue.get("text", ""))
            if not value.strip():
                raise HTTPException(status_code=409, detail="AI Review Active Revision cue 文字不可空白")
            cues.append({
                "text": value,
                "source_segment_ids": lineage,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "cue_id": raw_cue.get("cue_id"),
            })
        cues.sort(key=lambda c: (c["start_ms"], c["end_ms"]))
        from app.subtitles.ai_review import _validate_revision_cues
        _validate_revision_cues(directory, cues)
        return cues, "ai_review_active"

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
