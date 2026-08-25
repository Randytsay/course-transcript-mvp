"""AI subtitle organization review workflow.

Layered model (SOURCE BASELINE is immutable):

- SOURCE BASELINE: the original Chirp/ASR segments in subtitles.json /
  subtitles-corrected.json. segment_id, start_ms, end_ms and raw_text are
  never modified or deleted by this module.

- CANDIDATE REVISION: AI-proposed changes stored as pending candidates.
  Accept/reject/edit only mutates candidate state; the active revision is
  untouched until an explicit publish.

- ACTIVE SUBTITLE REVISION: derived presentation cues. Every derived cue
  traces back to source_segment_ids and reuses source timestamps only.
  Publishing creates a new immutable Revision snapshot; rollback creates a
  new revision whose content equals the target, never a destructive
  overwrite.

Exports (SRT/VTT/TXT/DOCX) render from the active revision by default; any
historical revision can be rendered explicitly.

AI proposals are validated fail-closed:
- unknown/invented segment ids are rejected;
- invented timestamps are rejected (derived cues reuse source boundaries);
- cross-segment reflows must cover contiguous adjacent segments;
- segments with distinct speakers are never merged;
- every derived cue must carry a source lineage mapping;
- text conservation: reflow may redistribute characters across cues but the
  concatenated content must equal the scope input modulo whitespace, unless
  the change is a pure per-segment correction handled elsewhere.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor

DATA_DIR = Path(__import__("os").environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOBS_DIR = DATA_DIR / "jobs"
router = APIRouter(prefix="/api/v1/subtitles", tags=["subtitles"])
_LOCK = threading.RLock()

CHANGE_TYPES = frozenset(
    {
        "asr_typo",
        "proper_noun",
        "semantic_asr_error",
        "punctuation",
        "line_break",
        "repetition_cleanup",
        "obvious_speech_correction",
        "cross_segment_reflow",
        "merge_adjacent",
        "split_for_readability",
        "mixed",
    }
)
RISKS = frozenset({"low", "medium", "high"})
MAX_LINES_PER_CUE = 2
MAX_CHARS_PER_LINE = 20


class CandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_type: str
    source_segment_ids: list[str] = Field(min_length=1, max_length=8)
    before: list[dict[str, Any]]
    after: list[dict[str, Any]] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    risk: str
    high_review_required: bool = True


class OrganizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    candidates: list[CandidateProposal] = Field(min_length=1, max_length=2000)


class CandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str
    decision: str = Field(pattern="^(accept|reject)$")
    edited_after: list[dict[str, Any]] | None = None


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=0)


# ---------------------------------------------------------------------------
# baseline + storage helpers
# ---------------------------------------------------------------------------


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", value) or value in {".", ".."}:
        raise HTTPException(status_code=404, detail="Subtitle not found")
    return value


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _directory(subtitle_id: str) -> Path:
    """Resolve a job subtitle directory (jobs only for the AI review flow)."""
    directory = JOBS_DIR / _safe_id(subtitle_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Subtitle not found")
    return directory


def baseline_segments(directory: Path) -> list[dict[str, Any]]:
    """Immutable SOURCE BASELINE read-only view."""
    for name in ("subtitles.json",):
        payload = _read_json(directory / name, {})
        items = payload.get("segments") if isinstance(payload, dict) else None
        if isinstance(items, list) and items:
            return [
                {
                    "segment_id": str(item["segment_id"]),
                    "start_ms": int(item["start_ms"]),
                    "end_ms": int(item["end_ms"]),
                    "raw_text": str(item.get("raw_text", item.get("text", ""))),
                    **(
                        {"speaker": str(item["speaker"])}
                        if item.get("speaker") is not None
                        else {}
                    ),
                }
                for item in items
                if isinstance(item, dict) and item.get("segment_id") is not None
            ]
    raise HTTPException(status_code=409, detail="Subtitle segments are not ready")


def _review_state(directory: Path) -> dict[str, Any]:
    state = _read_json(directory / "ai-subtitle-review.json", {})
    if not isinstance(state, dict):
        state = {}
    return {
        "revision": int(state.get("revision", 0)),
        "candidates": state.get("candidates") if isinstance(state.get("candidates"), list) else [],
        "revisions": state.get("revisions") if isinstance(state.get("revisions"), list) else [],
        "active_revision": state.get("active_revision"),
        "updated_at": state.get("updated_at"),
    }


def _save_state(directory: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _iso()
    _atomic_json(directory / "ai-subtitle-review.json", state)


def _revision_by_number(state: dict[str, Any], number: int | None) -> dict[str, Any] | None:
    for item in state["revisions"]:
        if int(item["revision"]) == number:
            return item
    return None


# ---------------------------------------------------------------------------
# validation (fail-closed)
# ---------------------------------------------------------------------------


def _validate_candidate(candidate: CandidateProposal, baseline_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if candidate.change_type not in CHANGE_TYPES:
        raise HTTPException(status_code=422, detail=f"未知的修改類型: {candidate.change_type}")
    if candidate.risk not in RISKS:
        raise HTTPException(status_code=422, detail=f"未知的風險等級: {candidate.risk}")

    ids = candidate.source_segment_ids
    for segment_id in ids:
        if segment_id not in baseline_index:
            # AI invented a segment id that does not exist in the baseline.
            raise HTTPException(status_code=422, detail=f"發明不存在的 segment: {segment_id}")

    numbers = [baseline_index[s]["_index"] for s in ids]
    if numbers != sorted(numbers):
        raise HTTPException(status_code=422, detail="source_segment_ids 必須依原始順序")
    if len(set(numbers)) != len(numbers):
        raise HTTPException(status_code=422, detail="source_segment_ids 重複")

    before_by_id = {}
    for item in candidate.before:
        key = str(item.get("segment_id", ""))
        before_by_id[key] = str(item.get("text", ""))
    if set(before_by_id) != set(ids):
        raise HTTPException(status_code=422, detail="before 與 source_segment_ids 不一致")
    for segment_id, text in before_by_id.items():
        expected = str(baseline_index[segment_id].get("working_text", baseline_index[segment_id]["raw_text"]))
        if text != expected:
            raise HTTPException(status_code=422, detail=f"before 文字與來源不一致: {segment_id}")

    seen_lineage: set[str] = set()
    total_after_chars = 0
    for cue in candidate.after:
        cue_ids = cue.get("source_segment_ids")
        if not isinstance(cue_ids, list) or not cue_ids:
            raise HTTPException(status_code=422, detail="每個 cue 必須有 source_segment_ids")
        for segment_id in cue_ids:
            if segment_id not in baseline_index:
                raise HTTPException(status_code=422, detail=f"cue 引用不存在的 segment: {segment_id}")
            if segment_id not in ids:
                raise HTTPException(status_code=422, detail=f"cue 超出候選範圍: {segment_id}")
            seen_lineage.add(segment_id)
        text = cue.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=422, detail="cue 文字不可空白")
        total_after_chars += len(text.strip())
        # No invented timestamps allowed anywhere in the payload.
        if "start_ms" in cue or "end_ms" in cue:
            raise HTTPException(status_code=422, detail="禁止發明 timestamp；時間僅能由來源 boundary 推導")

    if seen_lineage != set(ids):
        missing = sorted(set(ids) - seen_lineage)
        raise HTTPException(status_code=422, detail=f"缺少來源對應的 segment: {missing}")

    multi = len(ids) > 1
    if multi and candidate.change_type not in {
        "cross_segment_reflow", "merge_adjacent", "split_for_readability", "mixed",
    }:
        raise HTTPException(status_code=422, detail=f"{candidate.change_type} 僅允許單一 segment")

    # Reflow/split must span contiguous adjacent segments only.
    if multi and candidate.change_type in {"cross_segment_reflow", "split_for_readability"}:
        indices = [baseline_index[s]["_index"] for s in ids]
        if max(indices) - min(indices) != len(indices) - 1:
            raise HTTPException(status_code=422, detail="跨段整理必須是相鄰 segments")

    if candidate.change_type == "merge_adjacent":
        if len(candidate.after) != 1:
            raise HTTPException(status_code=422, detail="merge_adjacent 只能產生單一 cue")
        if len({baseline_index[s].get("speaker") for s in ids if baseline_index[s].get("speaker")}) > 1:
            raise HTTPException(status_code=422, detail="不同 speaker 的字幕不可合併")

    # Text conservation for reflow-type changes: same visible words overall.
    if candidate.change_type in {"cross_segment_reflow", "merge_adjacent"}:
        before_joined = "".join(before_by_id[s] for s in ids)
        after_joined = "".join(str(cue["text"]) for cue in candidate.after)
        strip = lambda value: re.sub(r"\s+", "", value)  # noqa: E731
        if strip(before_joined) != strip(after_joined):
            raise HTTPException(
                status_code=422,
                detail="跨段整理不可改變總字詞內容；文字修正請另建 correction candidate",
            )

    return {
        "change_id": f"cand-{uuid.uuid4().hex[:12]}",
        "change_type": candidate.change_type,
        "source_segment_ids": list(ids),
        "before": [
            {"segment_id": segment_id, "text": before_by_id[segment_id]} for segment_id in ids
        ],
        "after": [
            {"source_segment_ids": list(cue["source_segment_ids"]), "text": str(cue["text"])}
            for cue in candidate.after
        ],
        "reason": candidate.reason,
        "confidence": candidate.confidence,
        "risk": candidate.risk,
        "high_review_required": True,
        "status": "pending",
        "decision_note": None,
        "created_at": _iso(),
    }


def _resolve_cues(
    directory: Path,
    state: dict[str, Any],
    accepted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize derived cues from accepted candidates over the baseline.

    Timestamps always come from the referenced source boundaries — never
    invented. A single-source cue uses that segment's timing; a multi-source
    cue spans from the first source start to the last source end.
    """
    baseline = baseline_segments(directory)
    baseline_index = {item["segment_id"]: {**item, "_position": position} for position, item in enumerate(baseline)}
    consumed: set[str] = set()
    cues: list[dict[str, Any]] = []
    for candidate in accepted:
        cue_specs = []
        offset = 0
        for cue in candidate["after"]:
            cue_ids = [str(value) for value in cue["source_segment_ids"]]
            start = min(baseline_index[value]["start_ms"] for value in cue_ids)
            end = max(baseline_index[value]["end_ms"] for value in cue_ids)
            cue_specs.append(
                {
                    "text": str(cue["text"]),
                    "source_segment_ids": cue_ids,
                    "start_ms": start,
                    "end_ms": end,
                }
            )
            offset += 1
        for spec in cue_specs:
            consumed.update(spec["source_segment_ids"])
            cues.append(spec)
    for item in baseline:
        if item["segment_id"] in consumed:
            continue
        working = str(baseline_index[item["segment_id"]].get("working_text", item["raw_text"]))
        cues.append(
            {
                "text": working,
                "source_segment_ids": [item["segment_id"]],
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
            }
        )
    cues.sort(key=lambda cue: min(baseline_index[value]["_position"] for value in cue["source_segment_ids"]))
    for index, cue in enumerate(cues, 1):
        cue["cue_id"] = f"cue-{index:04d}"
    return cues


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _srt_time(value: int, separator: str = ",") -> str:
    value = max(0, int(value))
    hours, value = divmod(value, 3_600_000)
    minutes, value = divmod(value, 60_000)
    seconds, milliseconds = divmod(value, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def render_srt(cues: list[dict[str, Any]]) -> str:
    blocks = []
    for index, cue in enumerate(cues, 1):
        lines = _wrap_lines(cue["text"])
        blocks.append(
            f"{index}\n{_srt_time(cue['start_ms'])} --> {_srt_time(cue['end_ms'])}\n" + "\n".join(lines)
        )
    return "\n\n".join(blocks) + "\n"


def render_vtt(cues: list[dict[str, Any]]) -> str:
    body = ["WEBVTT", ""]
    for index, cue in enumerate(cues, 1):
        lines = _wrap_lines(cue["text"])
        body.append(str(index))
        body.append(f"{_srt_time(cue['start_ms'], '.')} --> {_srt_time(cue['end_ms'], '.')}")
        body.extend(lines)
        body.append("")
    return "\n".join(body)


def render_txt(cues: list[dict[str, Any]]) -> str:
    return "\n".join(re.sub(r"\s+", "", cue["text"]) for cue in cues) + "\n"


def _wrap_lines(text: str) -> list[str]:
    text = text.replace("\n", " ").strip()
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]
    midpoint = (len(text) + 1) // 2
    for pivot in range(midpoint, max(0, midpoint - 6), -1):
        if pivot < len(text) and text[pivot - 1] in "，。！？、；：":
            return [text[:pivot], text[pivot:]]
    return [text[:midpoint], text[midpoint:]]


def render_docx_bytes(cues: list[dict[str, Any]]) -> bytes:
    try:
        from docx import Document
        from io import BytesIO
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise HTTPException(status_code=503, detail="DOCX 匯出元件未安裝") from exc
    document = Document()
    document.add_heading("Course Transcript", level=0)
    buffer = BytesIO()
    for cue in cues:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(f"[{_srt_time(int(cue['start_ms']), ':')[:-4]}] ")
        run.bold = True
        paragraph.add_run(str(cue["text"]))
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("/{subtitle_id}/ai-review")
def get_ai_review_state(subtitle_id: str) -> dict[str, Any]:
    directory = _directory(subtitle_id)
    state = _review_state(directory)
    counts: dict[str, int] = {}
    for candidate in state["candidates"]:
        counts[candidate["status"]] = counts.get(candidate["status"], 0) + 1
    return {
        "subtitle_id": subtitle_id,
        "revision": state["revision"],
        "active_revision": state["active_revision"],
        "counts": counts,
        "total_candidates": len(state["candidates"]),
        "candidates": state["candidates"],
        "revisions": [
            {
                "revision": item["revision"],
                "created_at": item["created_at"],
                "source": item["source"],
                "content_sha256": item["content_sha256"],
                "cue_count": len(item["cues"]),
                "active": state["active_revision"] == item["revision"],
            }
            for item in state["revisions"]
        ],
    }


@router.post("/{subtitle_id}/ai-review/candidates")
def propose_candidates(subtitle_id: str, payload: OrganizeRequest, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    directory = _directory(subtitle_id)
    with _LOCK:
        state = _review_state(directory)
        if state["revision"] != payload.expected_revision:
            raise HTTPException(status_code=409, detail="審核狀態已更新，請重新載入")
        baseline = baseline_segments(directory)
        baseline_index = {
            item["segment_id"]: {**item, "_index": position}
            for position, item in enumerate(baseline)
        }
        created = []
        for candidate in payload.candidates:
            record = _validate_candidate(candidate, baseline_index)
            record["proposed_by"] = actor
            state["candidates"].append(record)
            created.append(record)
        _save_state(directory, state)
        return {
            "created": len(created),
            "total_candidates": len(state["candidates"]),
            "expected_revision": state["revision"],
            "candidates": created,
        }


@router.post("/{subtitle_id}/ai-review/candidates/decide")
def decide_candidate(subtitle_id: str, payload: CandidateDecision, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    directory = _directory(subtitle_id)
    with _LOCK:
        state = _review_state(directory)
        target = next((item for item in state["candidates"] if item["change_id"] == payload.change_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if target["status"] != "pending":
            raise HTTPException(status_code=409, detail="此建議已審核")
        after = target["after"]
        if payload.decision == "accept":
            if payload.edited_after is not None:
                replacement = CandidateProposal(
                    change_type=target["change_type"],
                    source_segment_ids=target["source_segment_ids"],
                    before=target["before"],
                    after=payload.edited_after,
                    reason=target["reason"],
                    confidence=target["confidence"],
                    risk=target["risk"],
                    high_review_required=True,
                )
                baseline = baseline_segments(directory)
                baseline_index = {
                    item["segment_id"]: {**item, "_index": position}
                    for position, item in enumerate(baseline)
                }
                validated = _validate_candidate(replacement, baseline_index)
                after = validated["after"]
                target["manually_edited"] = True
            target["status"] = "accepted"
        else:
            target["status"] = "rejected"
            if payload.edited_after is not None:
                raise HTTPException(status_code=422, detail="拒絕不可附帶修改內容")
        target["decided_by"] = actor
        target["decided_at"] = _iso()
        _save_state(directory, state)
        counts: dict[str, int] = {}
        for candidate in state["candidates"]:
            counts[candidate["status"]] = counts.get(candidate["status"], 0) + 1
        return {"change_id": payload.change_id, "status": target["status"], "counts": counts}


@router.post("/{subtitle_id}/ai-review/publish")
def publish_revision(subtitle_id: str, payload: PublishRequest, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    directory = _directory(subtitle_id)
    with _LOCK:
        state = _review_state(directory)
        if state["revision"] != payload.base_revision:
            raise HTTPException(status_code=409, detail="審核狀態已更新，請重新載入")
        pending = sum(1 for item in state["candidates"] if item["status"] == "pending")
        if pending:
            raise HTTPException(status_code=409, detail=f"尚有 {pending} 項建議未審核")
        accepted = [item for item in state["candidates"] if item["status"] == "accepted"]
        cues = _resolve_cues(directory, state, accepted)
        digest = hashlib.sha256(render_srt(cues).encode("utf-8")).hexdigest()
        number = state["revision"] + 1
        revision_record = {
            "revision": number,
            "created_at": _iso(),
            "created_by": actor,
            "source": "ai_subtitle_review_publish",
            "accepted_change_ids": [item["change_id"] for item in accepted],
            "rejected_change_ids": [
                item["change_id"] for item in state["candidates"] if item["status"] == "rejected"
            ],
            "cues": cues,
            "content_sha256": digest,
        }
        state["revisions"].append(revision_record)
        previous_active = state["active_revision"]
        state["active_revision"] = number
        state["revision"] = number
        # Reviewed decisions are folded into the published revision; keep the
        # audit trail inside the revision record itself.
        state["candidates"] = []
        _save_state(directory, state)
        return {
            "revision": number,
            "previous_active_revision": previous_active,
            "active_revision": state["active_revision"],
            "cue_count": len(cues),
            "accepted_count": len(accepted),
            "content_sha256": digest,
        }


@router.post("/{subtitle_id}/ai-review/revisions/{revision}/rollback")
def rollback_revision(subtitle_id: str, revision: int, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    directory = _directory(subtitle_id)
    with _LOCK:
        state = _review_state(directory)
        target = _revision_by_number(state, revision)
        if target is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        if revision == state["active_revision"]:
            raise HTTPException(status_code=409, detail="此版本已是 Active")
        cues = [dict(cue) for cue in target["cues"]]
        number = state["revision"] + 1
        record = {
            "revision": number,
            "created_at": _iso(),
            "created_by": actor,
            "source": "rollback",
            "rolled_back_from": revision,
            "accepted_change_ids": [],
            "rejected_change_ids": [],
            "cues": cues,
            "content_sha256": target["content_sha256"],
        }
        state["revisions"].append(record)
        state["active_revision"] = number
        state["revision"] = number
        _save_state(directory, state)
        return {"revision": number, "restored_from": revision, "active_revision": number}


@router.get("/{subtitle_id}/ai-review/export/{kind}")
@router.get("/{subtitle_id}/ai-review/export/{kind}/{revision}")
def export_revision(subtitle_id: str, kind: str, revision: int | None = None) -> Any:
    from fastapi import Response

    directory = _directory(subtitle_id)
    state = _review_state(directory)
    resolved = revision if revision is not None else state["active_revision"]
    if resolved is None:
        raise HTTPException(status_code=409, detail="尚無已發布的字幕版本")
    record = _revision_by_number(state, resolved)
    if record is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    cues = record["cues"]
    kind = kind.lower()
    if kind == "srt":
        return Response(render_srt(cues), media_type="application/x-subrip")
    if kind == "vtt":
        return Response(render_vtt(cues), media_type="text/vtt")
    if kind == "txt":
        return Response(render_txt(cues), media_type="text/plain")
    if kind == "docx":
        return Response(
            render_docx_bytes(cues),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{subtitle_id}-r{resolved}.docx"'},
        )
    raise HTTPException(status_code=422, detail="不支援的匯出格式")


@router.get("/{subtitle_id}/ai-review/baseline")
def get_baseline(subtitle_id: str) -> dict[str, Any]:
    directory = _directory(subtitle_id)
    baseline = baseline_segments(directory)
    state = _review_state(directory)
    working_by_id = {}
    if state["revisions"]:
        active = _revision_by_number(state, state["active_revision"])
        if active:
            for cue in active["cues"]:
                for segment_id in cue["source_segment_ids"]:
                    working_by_id.setdefault(segment_id, cue["text"])
    return {
        "subtitle_id": subtitle_id,
        "segments": [
            {**item, "working_text": working_by_id.get(item["segment_id"], item["raw_text"])}
            for item in baseline
        ],
    }
