"""Serve formal transcript segments only after the configured pipeline is complete."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.jobs.store import JobNotFound, JobStore


router = APIRouter()
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOBS_DIR = DATA_DIR / "jobs"
FORMAL_JOB_STATES = {"awaiting_review", "review", "completed"}
COMPLETE_CHUNK_STATES = {"SUCCEEDED", "EMPTY_SILENCE"}


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _job_record(job_id: str) -> dict[str, Any]:
    try:
        return JobStore(DATA_DIR / "course-transcript.db").get_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


def _job_dir(job_id: str) -> Path:
    if Path(job_id).name != job_id or job_id in {"", ".", ".."}:
        raise HTTPException(status_code=404, detail="Job not found")
    directory = JOBS_DIR / job_id
    if not directory.is_dir():
        raise HTTPException(status_code=409, detail="Formal transcript is not ready")
    return directory


def _all_chunks_complete(directory: Path) -> bool:
    plan = _read_json(directory / "chunk-plan.json", {})
    items = plan.get("chunks") if isinstance(plan, dict) else None
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        try:
            index = int(item["chunk_index"])
        except (KeyError, TypeError, ValueError):
            return False
        manifest = _read_json(
            directory / "chunks" / f"chunk-{index:03d}" / "manifest.json", {}
        )
        if not isinstance(manifest, dict) or manifest.get("status") not in COMPLETE_CHUNK_STATES:
            return False
    return True


def formal_ready(record: dict[str, Any], directory: Path) -> bool:
    if record.get("status") not in FORMAL_JOB_STATES:
        return False
    if not _all_chunks_complete(directory):
        return False
    required = [
        directory / "merged-words.json",
        directory / "subtitles.json",
        directory / "qa-report.json",
    ]
    if bool(record.get("enable_gemini_correction")):
        required.append(directory / "subtitles-corrected.json")
    return all(path.is_file() and path.stat().st_size > 0 for path in required)


def build_formal_segments(job_id: str) -> dict[str, list[dict[str, Any]]]:
    record = _job_record(job_id)
    directory = _job_dir(job_id)
    if not formal_ready(record, directory):
        raise HTTPException(status_code=409, detail="Formal transcript is not ready")

    raw_payload = _read_json(directory / "subtitles.json", {})
    corrected_payload = _read_json(directory / "subtitles-corrected.json", {})
    raw = raw_payload.get("segments") if isinstance(raw_payload, dict) else None
    corrected = (
        corrected_payload.get("segments")
        if isinstance(corrected_payload, dict)
        else None
    )
    raw = raw if isinstance(raw, list) else []
    corrected = corrected if isinstance(corrected, list) else []
    corrected_by_id = {
        str(item.get("segment_id", index + 1)): item
        for index, item in enumerate(corrected)
        if isinstance(item, dict)
    }
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id", index + 1))
        correction = corrected_by_id.get(segment_id, {})
        raw_text = str(item.get("raw_text", item.get("text", "")))
        corrected_text = str(
            correction.get("corrected_text", correction.get("text", raw_text))
        )
        result.append(
            {
                "segment_id": segment_id,
                "order": index,
                "start_ms": int(item.get("start_ms", 0)),
                "end_ms": int(item.get("end_ms", 0)),
                "raw_text": raw_text,
                "corrected_text": corrected_text,
                "uncertain_terms": correction.get("uncertain_terms", []),
                "revision": int(correction.get("revision", 0)),
            }
        )
    return {"segments": result}


@router.get("/api/v1/jobs/{job_id}/segments")
def formal_segments_endpoint(job_id: str) -> dict[str, list[dict[str, Any]]]:
    return build_formal_segments(job_id)
