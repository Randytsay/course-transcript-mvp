"""Owner-only endpoints for strict ChatGPT subtitle handoff re-entry."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor, _store
from app.jobs.store import JobConflict, JobNotFound
from app.jobs.workflow_mode import CHATGPT_HANDOFF, normalize_workflow_mode
from app.providers.chatgpt_handoff import (
    BUNDLE_DIRNAME,
    import_corrected_segments,
    import_corrected_srt,
)


router = APIRouter(prefix="/api/v1/jobs", tags=["chatgpt-handoff"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))


class ChatGPTHandoffSegmentEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=128)
    corrected_text: str = Field(min_length=1, max_length=20_000)


class ChatGPTHandoffImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    segment_edits: list[ChatGPTHandoffSegmentEdit] | None = Field(default=None, max_length=20_000)
    srt_text: str | None = Field(default=None, min_length=1, max_length=8_000_000)

class ChirpCompletenessRecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def _job(job_id: str) -> dict[str, Any]:
    try:
        return _store().get_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _snapshot_import_outputs(job_dir: Path) -> dict[Path, bytes | None]:
    paths = [
        job_dir / "subtitles-corrected.json",
        job_dir / "subtitles-corrected.srt",
        job_dir / "subtitles-corrected.vtt",
        job_dir / "transcript-corrected.txt",
        job_dir / BUNDLE_DIRNAME / "import-audit.json",
    ]
    return {path: path.read_bytes() if path.is_file() else None for path in paths}


def _restore_import_outputs(snapshot: dict[Path, bytes | None]) -> None:
    for path, previous in snapshot.items():
        if previous is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(previous)


@router.get("/{job_id}/chatgpt-handoff")
def get_chatgpt_handoff(job_id: str) -> dict[str, Any]:
    record = _job(job_id)
    job_dir = DATA_DIR / "jobs" / job_id
    manifest_path = job_dir / BUNDLE_DIRNAME / "handoff-manifest.json"
    manifest = None
    completeness = None
    completeness_path = job_dir / "chirp-completeness.json"
    if completeness_path.is_file():
        try:
            completeness = json.loads(completeness_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            completeness = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None
    return {
        "job_id": job_id,
        "workflow_mode": normalize_workflow_mode(record.get("workflow_mode")),
        "status": record["status"],
        "active_stage": record.get("active_stage"),
        "revision": int(record["revision"]),
        "ready": bool(manifest),
        "manifest": manifest,
        "timestamps_immutable": True,
        "preferred_import_format": "segment_edits",
        "chirp_completeness": completeness,
    }


@router.post("/{job_id}/chirp-completeness/recheck")
def recheck_chirp_completeness(
    job_id: str,
    payload: ChirpCompletenessRecheckRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    record = _job(job_id)
    if normalize_workflow_mode(record.get("workflow_mode")) != CHATGPT_HANDOFF:
        raise HTTPException(status_code=409, detail="此任務不是 ChatGPT Handoff 模式")
    try:
        updated = _store().requeue_chirp_completeness(
            job_id=job_id,
            expected_revision=payload.expected_revision,
            actor=actor,
        )
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "status": updated["status"],
        "active_stage": updated["active_stage"],
        "revision": int(updated["revision"]),
        "paid_operation_started": False,
        "next_action": "重新建立字幕並重跑 Chirp 完整性 Gate；既有 provider evidence 會被重用",
    }


@router.post("/{job_id}/chatgpt-handoff/import")
def import_chatgpt_handoff(
    job_id: str,
    payload: ChatGPTHandoffImportRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    record = _job(job_id)
    if normalize_workflow_mode(record.get("workflow_mode")) != CHATGPT_HANDOFF:
        raise HTTPException(status_code=409, detail="此任務不是 ChatGPT Handoff 模式")
    if int(record["revision"]) != payload.expected_revision:
        raise HTTPException(status_code=409, detail="任務已更新，請重新載入後再回灌")
    if record["status"] != "awaiting_review" or record.get("active_stage") != "chatgpt_handoff":
        raise HTTPException(status_code=409, detail="任務目前不在等待 ChatGPT 回灌階段")
    has_segments = bool(payload.segment_edits)
    has_srt = bool(payload.srt_text)
    if has_segments == has_srt:
        raise HTTPException(
            status_code=422,
            detail="請只提供 segment_edits 或 srt_text 其中一種；優先使用 segment_edits",
        )

    job_dir = DATA_DIR / "jobs" / job_id
    snapshot = _snapshot_import_outputs(job_dir)
    try:
        if payload.segment_edits:
            audit = import_corrected_segments(
                job_dir,
                segment_edits=[item.model_dump() for item in payload.segment_edits],
                actor=actor,
            )
        else:
            audit = import_corrected_srt(
                job_dir,
                srt_text=str(payload.srt_text or ""),
                actor=actor,
            )
        updated = _store().resume_after_chatgpt_handoff(
            job_id=job_id,
            expected_revision=payload.expected_revision,
            actor=actor,
        )
    except JobConflict as exc:
        _restore_import_outputs(snapshot)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        _restore_import_outputs(snapshot)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        _restore_import_outputs(snapshot)
        raise
    return {
        "job_id": job_id,
        "status": updated["status"],
        "active_stage": updated["active_stage"],
        "revision": int(updated["revision"]),
        "timestamps_immutable": True,
        "preferred_import_format": "segment_edits",
        "cue_count": audit["cue_count"],
        "next_action": "本機 cleanup / Golden Rules / QA，無 LLM provider 呼叫",
    }
