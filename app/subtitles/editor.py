"""Optional subtitle editor independent from the paid transcription pipeline.

Completed jobs are immediately usable. This router overlays manual edits on the
immutable Chirp/Gemini evidence, detects suspicious cues, supports exact batch
replacement with a preview, imports external SRT text, and republishes edited
SRT/TXT through the non-overwriting Drive transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor
from app.jobs.drive_publish import publish_outputs, source_parent_destination

DATA_DIR = Path(__import__("os").environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOBS_DIR = DATA_DIR / "jobs"
IMPORTED_DIR = DATA_DIR / "imported-subtitles"
router = APIRouter(prefix="/api/v1/subtitles", tags=["subtitles"])
_LOCK = threading.RLock()
SRT_TIMING = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


class SegmentEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=0)


class ReplacePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field(min_length=1, max_length=200)
    replacement: str = Field(min_length=1, max_length=200)
    subtitle_ids: list[str] = Field(min_length=1, max_length=100)


class ReplaceApplyRequest(ReplacePreviewRequest):
    expected_revisions: dict[str, int]


class ImportSrtRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    srt_text: str = Field(min_length=1, max_length=5_000_000)


class PublishEditedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    # Canonical concurrency check: when supplied, must match the current
    # canonical publication key or the request is rejected (409).
    expected_publication_key: str | None = None
    output_formats: list[Literal["srt", "txt"]] = Field(default_factory=lambda: ["srt", "txt"])


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", value) or value in {".", ".."}:
        raise HTTPException(status_code=404, detail="Subtitle not found")
    return value


def _job_record(subtitle_id: str) -> dict[str, Any] | None:
    database = DATA_DIR / "course-transcript.db"
    if not database.is_file():
        return None
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (subtitle_id,)).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def _directory(subtitle_id: str) -> tuple[Path, str]:
    safe = _safe_id(subtitle_id)
    job_dir = JOBS_DIR / safe
    if job_dir.is_dir():
        return job_dir, "job"
    imported = IMPORTED_DIR / safe
    if imported.is_dir():
        return imported, "imported"
    raise HTTPException(status_code=404, detail="Subtitle not found")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _base_segments(directory: Path) -> list[dict[str, Any]]:
    for name in ("subtitles-corrected.json", "subtitles.json"):
        payload = _read_json(directory / name, {})
        items = payload.get("segments") if isinstance(payload, dict) else None
        if isinstance(items, list) and items:
            result: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                raw = str(item.get("raw_text", item.get("text", "")))
                corrected = str(item.get("corrected_text", item.get("text", raw)))
                result.append(
                    {
                        **item,
                        "segment_id": str(item.get("segment_id", index + 1)),
                        "start_ms": int(item.get("start_ms", 0)),
                        "end_ms": int(item.get("end_ms", 0)),
                        "raw_text": raw,
                        "corrected_text": corrected,
                    }
                )
            return result
    raise HTTPException(status_code=409, detail="Subtitle segments are not ready")


def _edit_state(directory: Path) -> dict[str, Any]:
    state = _read_json(directory / "subtitle-editor.json", {})
    if not isinstance(state, dict):
        state = {}
    edits = state.get("edits")
    history = state.get("history")
    return {
        "revision": int(state.get("revision", 0)),
        "updated_at": state.get("updated_at"),
        "edits": edits if isinstance(edits, dict) else {},
        "history": history if isinstance(history, list) else [],
    }


def _suspicions(segment: dict[str, Any], text: str) -> list[str]:
    reasons: list[str] = []
    uncertain = segment.get("uncertain_terms")
    if isinstance(uncertain, list) and uncertain:
        reasons.append("Gemini 標記疑似詞彙")
    if segment.get("correction_fallback"):
        reasons.append("Gemini 校正回退原文")
    raw = str(segment.get("raw_text", ""))
    corrected = str(segment.get("corrected_text", raw))
    if raw and corrected and raw != corrected and SequenceMatcher(None, raw, corrected).ratio() < 0.55:
        reasons.append("Chirp 與 Gemini 差異較大")
    if not text.strip():
        reasons.append("空白字幕")
    if len(text) > 48:
        reasons.append("單段文字偏長")
    if re.search(r"(.)\1{3,}", text):
        reasons.append("疑似重複字元")
    if int(segment.get("end_ms", 0)) <= int(segment.get("start_ms", 0)):
        reasons.append("時間碼異常")
    return reasons


def _current_segments(directory: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = _edit_state(directory)
    edits = state["edits"]
    result: list[dict[str, Any]] = []
    for item in _base_segments(directory):
        segment_id = str(item["segment_id"])
        current = str(edits.get(segment_id, item["corrected_text"]))
        reasons = _suspicions(item, current)
        result.append(
            {
                "segment_id": segment_id,
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
                "raw_text": item["raw_text"],
                "ai_text": item["corrected_text"],
                "current_text": current,
                "manually_edited": segment_id in edits,
                "suspected": bool(reasons),
                "suspected_reasons": reasons,
                "uncertain_terms": item.get("uncertain_terms", []),
            }
        )
    return result, state


def _timestamp(value: int) -> str:
    hours, value = divmod(max(0, value), 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def _render_current(directory: Path, segments: list[dict[str, Any]], revision: int) -> dict[str, str]:
    output = directory / "subtitle-editor"
    output.mkdir(parents=True, exist_ok=True)
    srt = output / "current.srt"
    txt = output / "current.txt"
    current_json = output / "current.json"
    _atomic_text(
        srt,
        "\n\n".join(
            f"{index}\n{_timestamp(item['start_ms'])} --> {_timestamp(item['end_ms'])}\n{item['current_text']}"
            for index, item in enumerate(segments, 1)
        )
        + "\n",
    )
    _atomic_text(txt, "\n".join(item["current_text"] for item in segments) + "\n")
    _atomic_json(
        current_json,
        {"revision": revision, "generated_at": _iso(), "segments": segments},
    )
    return {"srt": str(srt), "txt": str(txt), "json": str(current_json)}


def _save_state(directory: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _iso()
    _atomic_json(directory / "subtitle-editor.json", state)
    segments, _ = _current_segments(directory)
    _render_current(directory, segments, int(state["revision"]))


def _summary(subtitle_id: str, directory: Path, kind: str) -> dict[str, Any]:
    try:
        segments, state = _current_segments(directory)
    except HTTPException:
        return {}
    record = _job_record(subtitle_id) if kind == "job" else None
    metadata = _read_json(directory / "metadata.json", {}) if kind == "imported" else {}
    source_name = (
        str(record.get("source_name")) if record else str(metadata.get("name", subtitle_id))
    )
    return {
        "id": subtitle_id,
        "kind": kind,
        "name": source_name,
        "status": str(record.get("status")) if record else "imported",
        "revision": state["revision"],
        "segment_count": len(segments),
        "suspected_count": sum(item["suspected"] for item in segments),
        "edited_count": sum(item["manually_edited"] for item in segments),
        "updated_at": state.get("updated_at") or (
            datetime.fromtimestamp(directory.stat().st_mtime, tz=UTC).isoformat()
        ),
        "can_publish_to_source": bool(record and str(record.get("source_path", "")).startswith("gdrive:")),
    }


def _parse_srt(text: str) -> list[dict[str, Any]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    segments: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip("\ufeff") for line in block.splitlines()]
        if len(lines) < 3:
            continue
        timing_index = 1 if lines[0].strip().isdigit() else 0
        match = SRT_TIMING.match(lines[timing_index].strip())
        if not match:
            continue
        values = [int(value) for value in match.groups()]
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        content = "\n".join(lines[timing_index + 1 :]).strip()
        if end <= start or not content:
            continue
        index = len(segments) + 1
        segments.append(
            {
                "segment_id": str(index),
                "start_ms": start,
                "end_ms": end,
                "raw_text": content,
                "corrected_text": content,
                "text": content,
                "uncertain_terms": [],
            }
        )
    if not segments:
        raise HTTPException(status_code=422, detail="No valid SRT cues found")
    return segments


@router.get("")
def list_subtitles() -> dict[str, list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    if JOBS_DIR.is_dir():
        for directory in sorted(JOBS_DIR.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
            if not directory.is_dir() or not ((directory / "subtitles.json").exists() or (directory / "subtitles-corrected.json").exists()):
                continue
            summary = _summary(directory.name, directory, "job")
            if summary:
                items.append(summary)
    if IMPORTED_DIR.is_dir():
        for directory in sorted(IMPORTED_DIR.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
            if directory.is_dir():
                summary = _summary(directory.name, directory, "imported")
                if summary:
                    items.append(summary)
    return {"subtitles": items}


@router.post("/import", status_code=201)
def import_srt(payload: ImportSrtRequest, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    segments = _parse_srt(payload.srt_text)
    subtitle_id = f"import-{uuid.uuid4().hex[:12]}"
    directory = IMPORTED_DIR / subtitle_id
    directory.mkdir(parents=True, exist_ok=False)
    _atomic_json(
        directory / "subtitles.json",
        {"source": "external_srt_import", "segments": segments},
    )
    _atomic_json(
        directory / "metadata.json",
        {"name": payload.name, "imported_by": actor, "imported_at": _iso()},
    )
    _save_state(directory, _edit_state(directory))
    return _summary(subtitle_id, directory, "imported")


def render_canonical(
    directory: Path,
    segments: list[dict[str, Any]],
    revision: int,
) -> dict[str, str]:
    """Render the canonical current subtitle for publication.

    Uses the AI Review active revision cues when present (cue-aware: merged
    and reflowed cues are preserved as-is); otherwise falls back to the
    per-segment editor rendering. Output files are identical in shape so the
    Drive publish transaction is unchanged.
    """
    from app.subtitles import canonical_state

    cues, source = canonical_state.canonical_cues(directory, segments)
    if source != "ai_review_active":
        return _render_current(directory, segments, revision)
    output = directory / "subtitle-editor"
    output.mkdir(parents=True, exist_ok=True)
    srt = output / "current.srt"
    txt = output / "current.txt"
    current_json = output / "current.json"
    _atomic_text(
        srt,
        "\n\n".join(
            f"{index}\n{_timestamp(cue['start_ms'])} --> {_timestamp(cue['end_ms'])}\n"
            + "\n".join(_wrap_cue_lines(str(cue["text"])))
            for index, cue in enumerate(cues, 1)
        )
        + "\n",
    )
    _atomic_text(txt, "\n".join(re.sub(r"\s+", " ", str(cue["text"])).strip() for cue in cues) + "\n")
    _atomic_json(
        current_json,
        {
            "revision": revision,
            "generated_at": _iso(),
            "canonical_source": source,
            "cues": cues,
            "segments": segments,
        },
    )
    return {"srt": str(srt), "txt": str(txt), "json": str(current_json), "source": source}


def _wrap_cue_lines(text: str) -> list[str]:
    text = text.replace("\n", " ").strip()
    if len(text) <= 20:
        return [text]
    midpoint = (len(text) + 1) // 2
    for pivot in range(midpoint, max(0, midpoint - 6), -1):
        if pivot < len(text) and text[pivot - 1] in "，。！？、；：":
            return [text[:pivot], text[pivot:]]
    return [text[:midpoint], text[midpoint:]]


@router.get("/{subtitle_id}")
def get_subtitle(subtitle_id: str) -> dict[str, Any]:
    directory, kind = _directory(subtitle_id)
    segments, state = _current_segments(directory)
    summary = _summary(subtitle_id, directory, kind)
    # Canonical truth: when an AI Review active revision exists its cue list
    # (possibly merged/reflowed) is the current subtitle, not per-segment
    # editor state. Editor rows keep raw evidence + their own text for
    # traceability; the canonical cues are returned alongside.
    from app.subtitles import canonical_state

    cues, canonical_source = canonical_state.canonical_cues(directory, segments)
    return {
        **summary,
        "segments": segments,
        "history_count": len(state["history"]),
        "canonical_source": canonical_source,
        "canonical_cues": cues,
    }


@router.patch("/{subtitle_id}/segments/{segment_id}")
def edit_segment(
    subtitle_id: str,
    segment_id: str,
    payload: SegmentEditRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    directory, _ = _directory(subtitle_id)
    with _LOCK:
        from app.subtitles import canonical_state
        canonical_state.ensure_editor_mutation_allowed(directory)
        segments, state = _current_segments(directory)
        if state["revision"] != payload.expected_revision:
            raise HTTPException(status_code=409, detail="字幕已更新，請重新載入")
        selected = next((item for item in segments if item["segment_id"] == segment_id), None)
        if selected is None:
            raise HTTPException(status_code=404, detail="Segment not found")
        new_text = payload.text.strip()
        previous = selected["current_text"]
        if new_text == previous:
            return {"revision": state["revision"], "segment": selected}
        state["revision"] += 1
        state["edits"][segment_id] = new_text
        state["history"].append(
            {
                "revision": state["revision"],
                "type": "segment_edit",
                "segment_id": segment_id,
                "before": previous,
                "after": new_text,
                "actor": actor,
                "created_at": _iso(),
            }
        )
        _save_state(directory, state)
        updated, _ = _current_segments(directory)
        return {
            "revision": state["revision"],
            "segment": next(item for item in updated if item["segment_id"] == segment_id),
        }


@router.post("/replace/preview")
def preview_replace(payload: ReplacePreviewRequest) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    revisions: dict[str, int] = {}
    for subtitle_id in payload.subtitle_ids:
        directory, _ = _directory(subtitle_id)
        from app.subtitles import canonical_state
        canonical_state.ensure_editor_mutation_allowed(directory)
        segments, state = _current_segments(directory)
        revisions[subtitle_id] = state["revision"]
        for item in segments:
            count = item["current_text"].count(payload.search)
            if count:
                matches.append(
                    {
                        "subtitle_id": subtitle_id,
                        "segment_id": item["segment_id"],
                        "count": count,
                        "before": item["current_text"],
                        "after": item["current_text"].replace(payload.search, payload.replacement),
                    }
                )
    return {
        "match_count": sum(item["count"] for item in matches),
        "segment_count": len(matches),
        "subtitle_count": len({item["subtitle_id"] for item in matches}),
        "expected_revisions": revisions,
        "matches": matches[:500],
        "truncated": len(matches) > 500,
    }


@router.post("/replace/apply")
def apply_replace(payload: ReplaceApplyRequest, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    changed_segments = 0
    changed_occurrences = 0
    with _LOCK:
        loaded: list[tuple[str, Path, list[dict[str, Any]], dict[str, Any]]] = []
        for subtitle_id in payload.subtitle_ids:
            directory, _ = _directory(subtitle_id)
            from app.subtitles import canonical_state
            canonical_state.ensure_editor_mutation_allowed(directory)
            segments, state = _current_segments(directory)
            if payload.expected_revisions.get(subtitle_id) != state["revision"]:
                raise HTTPException(status_code=409, detail=f"{subtitle_id} 已更新，請重新預覽")
            loaded.append((subtitle_id, directory, segments, state))
        for subtitle_id, directory, segments, state in loaded:
            changes: list[dict[str, Any]] = []
            for item in segments:
                count = item["current_text"].count(payload.search)
                if not count:
                    continue
                after = item["current_text"].replace(payload.search, payload.replacement)
                state["edits"][item["segment_id"]] = after
                changes.append(
                    {
                        "segment_id": item["segment_id"],
                        "before": item["current_text"],
                        "after": after,
                        "count": count,
                    }
                )
                changed_segments += 1
                changed_occurrences += count
            if changes:
                state["revision"] += 1
                state["history"].append(
                    {
                        "revision": state["revision"],
                        "type": "batch_replace",
                        "search": payload.search,
                        "replacement": payload.replacement,
                        "changes": changes,
                        "actor": actor,
                        "created_at": _iso(),
                    }
                )
                _save_state(directory, state)
    return {
        "changed_segments": changed_segments,
        "changed_occurrences": changed_occurrences,
        "subtitle_count": len(payload.subtitle_ids),
    }


@router.post("/{subtitle_id}/publish")
def publish_edited(
    subtitle_id: str,
    payload: PublishEditedRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    directory, kind = _directory(subtitle_id)
    if kind != "job":
        raise HTTPException(status_code=409, detail="Imported subtitle has no original Drive destination")
    record = _job_record(subtitle_id)
    if not record or not str(record.get("source_path", "")).startswith("gdrive:"):
        raise HTTPException(status_code=409, detail="Original Drive source is unavailable")
    from app.subtitles import canonical_state

    identity = canonical_state.publication_identity(directory)
    segments, state = _current_segments(directory)
    expected_key = payload.expected_publication_key
    if expected_key is not None and expected_key != identity["publication_key"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Canonical content changed since load "
                f"(expected {expected_key}, current {identity['publication_key']}); reload"
            ),
        )
    if (
        identity["canonical_source"] == "editor"
        and (state["revision"] != payload.expected_revision or state["revision"] < 1)
    ):
        raise HTTPException(status_code=409, detail="Edited subtitle revision changed or has no edits")
    rendered = render_canonical(directory, segments, state["revision"])
    publication_key = identity["publication_key"]
    publish_dir = directory / "editor-publish" / f"canonical-{publication_key.replace(':', '-')}"
    publish_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rendered["srt"], publish_dir / "subtitles-corrected.srt")
    shutil.copy2(rendered["txt"], publish_dir / "transcript-corrected.txt")
    result = publish_outputs(
        publish_dir,
        source_name=str(record["source_name"]),
        destination=source_parent_destination(str(record["source_path"])),
        output_formats=payload.output_formats,
        authorized=True,
    )
    state["history"].append(
        {
            "revision": state["revision"],
            "publication_key": publication_key,
            "canonical_source": identity["canonical_source"],
            "canonical_revision": identity["canonical_revision"],
            "type": "drive_publish",
            "actor": actor,
            "created_at": _iso(),
            "output_formats": payload.output_formats,
            "backup_count": result.get("backup_count", 0),
        }
    )
    _save_state(directory, state)
    return {
        "status": result.get("status"),
        "publication_key": publication_key,
        "canonical_source": identity["canonical_source"],
        "canonical_revision": identity["canonical_revision"],
        "revision": state["revision"],
        "published_revision": state["revision"],
        "current_revision": state["revision"],
        "zero_edit_review": False,
        "backup_count": result.get("backup_count", 0),
        "files": result.get("files", {}),
    }
