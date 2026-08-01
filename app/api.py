"""API for the Course Transcript MVP web workspace.

This boundary exposes derived job metadata plus a guarded, non-paid preflight
workflow. It never reads the service-account key, rclone configuration, GCS
operation details, or an arbitrary local filesystem path supplied by a browser.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.jobs import CostConfig, JobConflict, JobNotFound, JobStore
from app.jobs.source import (
    SourceInspectionError,
    inspect_rclone_selection,
    inspect_rclone_source,
    list_rclone_directory,
)

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", APP_ROOT / "data"))
JOBS_DIR = DATA_DIR / "jobs"
ARTIFACT_ALLOWLIST = frozenset(
    {
        "subtitles.srt",
        "subtitles.vtt",
        "subtitles.ass",
        "subtitles-corrected.srt",
        "subtitles-corrected.vtt",
        "subtitles-corrected.ass",
        "subtitles.json",
        "subtitles-corrected.json",
        "transcript-raw.txt",
        "transcript-raw.md",
        "transcript-corrected.txt",
        "transcript-corrected.md",
        "transcript-timestamped.txt",
        "transcript-segments.csv",
        "transcript.json",
        "transcript.csv",
        "transcript.docx",
        "transcript.pdf",
        "transcript_raw.txt",
        "transcript_corrected.txt",
        "transcript_timestamped.txt",
        "transcript.srt",
        "transcript.vtt",
        "glossary_candidates.csv",
        "glossary_decisions.yaml",
        "join_qa.json",
        "qa_report.json",
        "qa_report.html",
        "usage_report.json",
        "processing_manifest.json",
        "export-manifest.json",
        "qa-report.json",
        "qa-report.md",
        "merged-words.json",
        "merge-decisions.json",
        "join-qa.json",
    }
)

app = FastAPI(title="Course Transcript MVP API", version="0.4.0")

# The deployed frontend should use same-origin /api through a reverse proxy.
# CORS is restricted to explicit local development origins only.
origins = [item.strip() for item in os.environ.get(
    "COURSE_TRANSCRIPT_CORS_ORIGINS", "http://localhost:3000"
).split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Accept", "Content-Type"],
)

_store_cache: tuple[Path, JobStore] | None = None


class SourceInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(min_length=4, max_length=2048)


class DriveBrowseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(default="gdrive:", min_length=4, max_length=2048)


class BatchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection_mode: Literal["files", "folder"]
    source_paths: list[str] = Field(min_length=1, max_length=100)


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    language_code: str = Field(default="cmn-Hant-TW", pattern=r"^[A-Za-z-]{2,24}$")
    profile: str = Field(default="highest_accuracy", pattern=r"^highest_accuracy$")
    enable_gemini_correction: bool = True
    enable_subtitles: bool = True
    require_human_review: bool = True


class CreateBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    language_code: str = Field(default="cmn-Hant-TW", pattern=r"^[A-Za-z-]{2,24}$")
    profile: str = Field(default="highest_accuracy", pattern=r"^highest_accuracy$")
    enable_gemini_correction: bool = True
    enable_subtitles: bool = True
    require_human_review: bool = True


class ApproveJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    confirmed_estimated_cost_usd: Decimal = Field(gt=0)


class ApproveBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    confirmed_estimated_cost_usd: Decimal = Field(gt=0)


class ReviewTermDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["confirmed", "ignored"]
    approved_value: str | None = Field(default=None, max_length=200)
    scope: Literal["session", "course", "instructor", "global"] = "session"


class JobActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class RetryStageRequest(JobActionRequest):
    stage: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")


def _store() -> JobStore:
    global _store_cache
    path = DATA_DIR / "course-transcript.db"
    if _store_cache is None or _store_cache[0] != path:
        _store_cache = (path, JobStore(path))
    return _store_cache[1]


def _cost_config() -> CostConfig:
    return CostConfig.from_env()


def _mutation_actor(request: Request) -> str:
    require_access = os.environ.get(
        "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS", "false"
    ).lower() in {"1", "true", "yes"}
    actor = request.headers.get("Cf-Access-Authenticated-User-Email")
    access_assertion = request.headers.get("Cf-Access-Jwt-Assertion")
    if require_access and (not actor or not access_assertion):
        raise HTTPException(status_code=401, detail="Cloudflare Access identity required")

    public_origin = os.environ.get("COURSE_TRANSCRIPT_PUBLIC_ORIGIN", "").rstrip("/")
    request_origin = request.headers.get("Origin", "").rstrip("/")
    if require_access and (not public_origin or request_origin != public_origin):
        raise HTTPException(status_code=403, detail="Invalid request origin")
    return actor or "local-development"


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _job_dir(job_id: str) -> Path:
    # A job id must name an immediate directory beneath the controlled jobs root.
    if Path(job_id).name != job_id or job_id in {"", ".", ".."}:
        raise HTTPException(status_code=404, detail="Job not found")
    directory = JOBS_DIR / job_id
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")
    return directory


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "未知"
    total = max(0, round(float(seconds)))
    return f"{total // 3600:02}:{(total % 3600) // 60:02}:{total % 60:02}"


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _chunk_manifests(directory: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in sorted((directory / "chunks").glob("chunk-*/manifest.json")):
        payload = _read_json(path, {})
        if isinstance(payload, dict):
            manifests.append(payload)
    return manifests


def _pipeline(directory: Path, qa: dict[str, Any] | None) -> list[dict[str, str]]:
    chunks = _chunk_manifests(directory)
    succeeded = sum(item.get("status") == "SUCCEEDED" for item in chunks)
    chunk_total = len(chunks)
    merged = (directory / "merged-words.json").exists()
    subtitles = (directory / "subtitles.json").exists()
    corrected = (directory / "subtitles-corrected.json").exists()
    qa_exists = (directory / "qa-report.json").exists()
    qa_unsafe = bool(qa and qa.get("status") not in {None, "PASS"})

    def state(done: bool, active: bool = False, warning: bool = False) -> str:
        if warning:
            return "warning"
        if done:
            return "completed"
        return "running" if active else "pending"

    return [
        {"id": "source", "label": "來源準備", "detail": "音訊已正規化" if (directory / "normalized.flac").exists() else "等待來源", "status": state((directory / "normalized.flac").exists())},
        {"id": "chirp", "label": "Chirp 時間軸", "detail": f"{succeeded} / {chunk_total} 分段完成" if chunk_total else "尚未建立分段", "status": state(chunk_total > 0 and succeeded == chunk_total, chunk_total > succeeded)},
        {"id": "merge", "label": "字詞接合", "detail": "已建立合併時間軸" if merged else "等待 Chirp 完成", "status": state(merged, chunk_total > 0 and succeeded == chunk_total and not merged)},
        {"id": "subtitles", "label": "固定字幕", "detail": "已建立固定字幕段" if subtitles else "等待接合", "status": state(subtitles, merged and not subtitles)},
        {"id": "gemini", "label": "Gemini 校正", "detail": "校正結果已保留" if corrected else "尚未開始或待重建", "status": state(corrected, subtitles and not corrected)},
        {"id": "qa", "label": "QA 審查", "detail": "需修正後再人工審查" if qa_unsafe else ("已產生 QA 報告，等待人工確認" if qa_exists else "等待輸出"), "status": state(qa_exists and not qa_unsafe, corrected and not qa_exists, qa_unsafe)},
    ]


def _job_summary(directory: Path) -> dict[str, Any]:
    qa = _read_json(directory / "qa-report.json")
    qa = qa if isinstance(qa, dict) else None
    subtitle = _read_json(directory / "subtitles.json", {})
    subtitle = subtitle if isinstance(subtitle, dict) else {}
    words = int((qa or {}).get("chirp", {}).get("word_count", 0) or 0)
    duration_seconds = (qa or {}).get("audio", {}).get("duration_ms")
    if duration_seconds is not None:
        duration_seconds = float(duration_seconds) / 1000
    if duration_seconds is None:
        duration_seconds = subtitle.get("total_duration_ms", 0) / 1000
    stages = _pipeline(directory, qa)
    failed = any(item["status"] == "failed" for item in stages)
    review = any(item["status"] == "warning" for item in stages) or (directory / "subtitles.json").exists()
    completed = all(item["status"] == "completed" for item in stages)
    # A local QA pass is ready for human review, not permission to publish.
    status = "failed" if failed else "review" if review else "completed" if completed else "transcribing"
    completed_count = sum(item["status"] == "completed" for item in stages)
    progress = round(completed_count * 100 / len(stages))
    source = next(iter(sorted(directory.glob("source.*"))), None)
    timestamp_source = max((p for p in directory.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, default=directory)
    return {
        "id": directory.name,
        "filename": source.name if source else directory.name,
        "source_path": "已登記的唯讀 Drive 來源",
        "course": directory.name.replace("-", " "),
        "duration": _format_duration(duration_seconds),
        "duration_seconds": round(float(duration_seconds or 0), 3),
        "progress": progress,
        "status": status,
        "created_at": _iso_mtime(directory),
        "updated_at": _iso_mtime(timestamp_source),
        "language": "cmn-Hant-TW",
        "model": "Chirp 3 + Gemini 3.6 Flash",
        "words": words,
        "review_terms": len(_read_json(directory / "review-terms.json", []) or []),
        "pipeline": stages,
        "active_stage": next(
            (item["id"] for item in stages if item["status"] == "running"),
            "review" if status == "review" else None,
        ),
        "stage_detail": next(
            (item["detail"] for item in stages if item["status"] in {"running", "warning"}),
            None,
        ),
        "error": None,
        "estimated_cost_usd": None,
        "reserved_cost_usd": "0",
        "actual_cost_usd": "0",
        "pricing_version": None,
        "revision": 0,
    }


def _database_pipeline(record: dict[str, Any]) -> list[dict[str, str]]:
    status = record["status"]
    source_state = (
        "running"
        if status == "preflight"
        else "warning"
        if status == "awaiting_confirmation"
        else "completed"
        if status not in {"failed"}
        else "failed"
    )
    source_detail = {
        "preflight": "安全下載與媒體檢查尚未完成",
        "awaiting_confirmation": "等待人工確認預估費用",
        "queued": "費用已確認，等待 Worker",
        "failed": record.get("error") or "來源準備失敗",
    }.get(status, "來源與費用已確認")
    return [
        {"id": "source", "label": "來源與費用", "detail": source_detail, "status": source_state},
        {"id": "chirp", "label": "Chirp 時間軸", "detail": "尚未開始", "status": "pending"},
        {"id": "merge", "label": "字詞接合", "detail": "等待 Chirp", "status": "pending"},
        {"id": "subtitles", "label": "固定字幕", "detail": "等待接合", "status": "pending"},
        {"id": "gemini", "label": "Gemini 校正", "detail": "等待固定字幕", "status": "pending"},
        {"id": "qa", "label": "QA 審查", "detail": "等待輸出", "status": "pending"},
    ]


def _database_job_summary(record: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = float(record["duration_seconds"] or 0)
    directory = JOBS_DIR / record["id"]
    qa = _read_json(directory / "qa-report.json") if directory.is_dir() else None
    qa = qa if isinstance(qa, dict) else None
    pipeline = (
        _pipeline(directory, qa)
        if directory.is_dir()
        and record["status"]
        not in {"preflight", "awaiting_confirmation", "queued", "failed"}
        else _database_pipeline(record)
    )
    active_to_pipeline = {
        "download": "source",
        "normalize": "source",
        "chirp": "chirp",
        "merge": "merge",
        "segment": "subtitles",
        "correction": "gemini",
        "export": "qa",
        "qa": "qa",
        "validation": "qa",
    }
    active_pipeline_id = active_to_pipeline.get(record.get("active_stage"))
    if active_pipeline_id and record["status"] not in {
        "awaiting_review",
        "failed",
        "paused",
    }:
        for item in pipeline:
            if item["id"] == active_pipeline_id and item["status"] != "completed":
                item["status"] = "running"
                item["detail"] = record["stage_detail"] or item["detail"]
    return {
        "id": record["id"],
        "filename": record["source_name"],
        "source_path": record["source_path"],
        "course": Path(record["source_name"]).stem,
        "duration": _format_duration(duration_seconds) if duration_seconds else "待檢查",
        "duration_seconds": duration_seconds,
        "progress": int(record["progress"]),
        "status": record["status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "language": record["language_code"],
        "model": "Chirp 3 + Gemini 3.6 Flash",
        "words": int((qa or {}).get("chirp", {}).get("word_count", 0) or 0),
        "review_terms": len(_read_json(directory / "review-terms.json", []) or []),
        "pipeline": pipeline,
        "active_stage": record["active_stage"],
        "stage_detail": record["stage_detail"],
        "error": record["error"],
        "batch_id": record.get("batch_id"),
        "chirp_max_parallel_chunks": record.get("chirp_max_parallel_chunks", 3),
        "estimated_cost_usd": record["estimated_cost_usd"],
        "reserved_cost_usd": record["reserved_cost_usd"],
        "actual_cost_usd": record["actual_cost_usd"],
        "pricing_version": record["pricing_version"],
        "revision": record["revision"],
    }


def _database_job(job_id: str) -> dict[str, Any] | None:
    try:
        return _store().get_job(job_id)
    except JobNotFound:
        return None


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    database_available = False
    try:
        _store()
        database_available = True
    except (OSError, sqlite3.Error):
        database_available = False
    return {
        "status": "ok",
        "api_version": app.version,
        "jobs_directory_available": JOBS_DIR.is_dir(),
        "database_available": database_available,
    }


@app.get("/api/v1/jobs")
def list_jobs() -> dict[str, list[dict[str, Any]]]:
    records = _store().list_jobs()
    jobs = [_database_job_summary(record) for record in records]
    database_ids = {record["id"] for record in records}
    if JOBS_DIR.exists():
        jobs.extend(
            _job_summary(path)
            for path in JOBS_DIR.iterdir()
            if path.is_dir() and path.name not in database_ids
        )
    jobs.sort(key=lambda job: job["updated_at"], reverse=True)
    return {"jobs": jobs}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    record = _database_job(job_id)
    return _database_job_summary(record) if record else _job_summary(_job_dir(job_id))


@app.get("/api/v1/jobs/{job_id}/events")
def get_job_events(job_id: str) -> dict[str, Any]:
    try:
        events = _store().list_job_events(job_id)
    except JobNotFound as exc:
        if (JOBS_DIR / job_id).is_dir():
            return {"events": []}
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"events": events}


def _job_action_result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "job": _database_job_summary(record),
        "paid_operation_started": False,
    }


@app.post("/api/v1/jobs/{job_id}/pause")
def pause_job(
    job_id: str,
    payload: JobActionRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        return _job_action_result(
            _store().pause_job(
                job_id=job_id,
                expected_revision=payload.expected_revision,
                actor=actor,
            )
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/jobs/{job_id}/resume")
def resume_job(
    job_id: str,
    payload: JobActionRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        return _job_action_result(
            _store().resume_job(
                job_id=job_id,
                expected_revision=payload.expected_revision,
                actor=actor,
            )
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/jobs/{job_id}/retry-stage")
def retry_stage(
    job_id: str,
    payload: RetryStageRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        return _job_action_result(
            _store().retry_failed_stage(
                job_id=job_id,
                expected_revision=payload.expected_revision,
                stage=payload.stage,
                actor=actor,
            )
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/jobs/{job_id}/segments")
def get_segments(job_id: str) -> dict[str, list[dict[str, Any]]]:
    if not (JOBS_DIR / job_id).is_dir() and _database_job(job_id):
        return {"segments": []}
    directory = _job_dir(job_id)
    raw = _read_json(directory / "subtitles.json", {}).get("segments", [])
    corrected = _read_json(directory / "subtitles-corrected.json", {}).get("segments", [])
    corrected_by_id = {str(item.get("segment_id", index + 1)): item for index, item in enumerate(corrected)}
    result = []
    for index, item in enumerate(raw):
        segment_id = str(item.get("segment_id", index + 1))
        correction = corrected_by_id.get(segment_id, {})
        raw_text = str(item.get("raw_text", item.get("text", "")))
        result.append({
            "segment_id": segment_id,
            "order": index,
            "start_ms": int(item.get("start_ms", 0)),
            "end_ms": int(item.get("end_ms", 0)),
            "raw_text": raw_text,
            "corrected_text": str(correction.get("corrected_text", correction.get("text", raw_text))),
            "uncertain_terms": correction.get("uncertain_terms", []),
            "revision": int(correction.get("revision", 0)),
        })
    return {"segments": result}


@app.get("/api/v1/jobs/{job_id}/review-terms")
def get_review_terms(job_id: str) -> dict[str, list[dict[str, Any]]]:
    if not (JOBS_DIR / job_id).is_dir() and _database_job(job_id):
        return {"review_terms": []}
    directory = _job_dir(job_id)
    terms = _read_json(directory / "review-terms.json", [])
    return {"review_terms": terms if isinstance(terms, list) else []}


@app.patch("/api/v1/jobs/{job_id}/review-terms/{term_id}")
def decide_review_term(
    job_id: str,
    term_id: str,
    payload: ReviewTermDecisionRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    if not re.fullmatch(r"[a-f0-9]{16}", term_id):
        raise HTTPException(status_code=404, detail="Review term not found")
    directory = _job_dir(job_id)
    path = directory / "review-terms.json"
    terms = _read_json(path, [])
    if not isinstance(terms, list):
        raise HTTPException(status_code=409, detail="Review terms are invalid")
    selected = next(
        (
            item
            for item in terms
            if isinstance(item, dict) and item.get("id") == term_id
        ),
        None,
    )
    if selected is None:
        raise HTTPException(status_code=404, detail="Review term not found")
    approved_value = (payload.approved_value or selected.get("suggestion") or "").strip()
    if payload.action == "confirmed" and not approved_value:
        raise HTTPException(status_code=422, detail="Confirmed term needs a value")
    decided_at = datetime.now(UTC).isoformat()
    selected.update(
        {
            "status": payload.action,
            "scope": payload.scope,
            "approved_value": approved_value if payload.action == "confirmed" else None,
            "decided_by": actor,
            "decided_at": decided_at,
        }
    )
    _atomic_json(path, terms)
    decisions_path = directory / "term-decisions.json"
    decisions = _read_json(decisions_path, [])
    decisions = decisions if isinstance(decisions, list) else []
    decisions.append(
        {
            "term_id": term_id,
            "action": payload.action,
            "scope": payload.scope,
            "approved_value": selected["approved_value"],
            "actor": actor,
            "decided_at": decided_at,
            "applied_to_transcript": False,
        }
    )
    _atomic_json(decisions_path, decisions)
    if _database_job(job_id):
        _store().append_audit_event(
            job_id=job_id,
            event_type="review_term_decided",
            actor=actor,
            payload={
                "term_id": term_id,
                "action": payload.action,
                "scope": payload.scope,
                "applied_to_transcript": False,
            },
        )
    return {
        "term": selected,
        "original_transcript_modified": False,
        "decision_recorded": True,
    }


@app.get("/api/v1/jobs/{job_id}/artifacts")
def get_artifacts(job_id: str) -> dict[str, list[dict[str, Any]]]:
    if not (JOBS_DIR / job_id).is_dir() and _database_job(job_id):
        return {"artifacts": []}
    directory = _job_dir(job_id)
    artifacts = []
    for name in sorted(ARTIFACT_ALLOWLIST):
        path = directory / name
        if path.is_file():
            artifacts.append({"id": name, "name": name, "size_bytes": path.stat().st_size, "updated_at": _iso_mtime(path)})
    return {"artifacts": artifacts}


@app.get("/api/v1/jobs/{job_id}/artifacts/{artifact_name}")
def open_artifact(job_id: str, artifact_name: str) -> FileResponse:
    """Serve only derived, allowlisted artifacts from a controlled job directory."""
    if artifact_name not in ARTIFACT_ALLOWLIST:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = _job_dir(job_id) / artifact_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path, filename=artifact_name, content_disposition_type="inline")


@app.get("/api/v1/costs")
def get_costs() -> dict[str, Any]:
    config = _cost_config()
    result = _store().cost_summary(config.project_limit_usd)
    result.update(
        {
            "warning_thresholds_usd": [
                str(value) for value in config.warning_thresholds_usd
            ],
            "pricing_version": config.pricing_version,
        }
    )
    return result


@app.post("/api/v1/drive/browse")
def browse_drive(
    payload: DriveBrowseRequest,
    request: Request,
) -> dict[str, Any]:
    _mutation_actor(request)
    try:
        current_path, entries = list_rclone_directory(payload.source_path)
    except SourceInspectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    allowed_root = os.environ.get(
        "COURSE_TRANSCRIPT_ALLOWED_SOURCE_PREFIX", "gdrive:"
    ).rstrip("/")
    relative = current_path[len(allowed_root) :].strip("/")
    parent_path = None
    if relative:
        parent_relative = str(Path(relative).parent).replace("\\", "/")
        separator = "" if allowed_root.endswith(":") else "/"
        parent_path = (
            allowed_root
            if parent_relative in {"", "."}
            else f"{allowed_root}{separator}{parent_relative}"
        )
    return {
        "current_path": current_path,
        "parent_path": parent_path,
        "entries": [entry.to_dict() for entry in entries],
        "supported_extensions": sorted(
            {
                Path(entry.name).suffix.lower()
                for entry in entries
                if entry.supported_media
            }
        ),
        "paid_operation_started": False,
    }


@app.post("/api/v1/drive/preview-batch")
def preview_drive_batch(
    payload: BatchPreviewRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        metadata = inspect_rclone_selection(
            selection_mode=payload.selection_mode,
            source_paths=payload.source_paths,
        )
        batch_preview = _store().create_batch_preview(
            selection_mode=payload.selection_mode,
            source_root=(
                payload.source_paths[0] if payload.selection_mode == "folder" else None
            ),
            items=[item.to_dict() for item in metadata],
            actor=actor,
        )
    except SourceInspectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "batch_preview_id": batch_preview["id"],
        "selection_mode": batch_preview["selection_mode"],
        "source_root": batch_preview["source_root"],
        "item_count": batch_preview["item_count"],
        "total_size_bytes": batch_preview["total_size_bytes"],
        "expires_at": batch_preview["expires_at"],
        "items": [
            {
                "preview_id": item["id"],
                "source_path": item["source_path"],
                "name": item["source_name"],
                "size_bytes": item["size_bytes"],
                "modified_at": item["modified_at"],
            }
            for item in batch_preview["items"]
        ],
        "preflight_required": True,
        "paid_operation_started": False,
    }


@app.post("/api/v1/drive/inspect")
def inspect_drive_source(
    payload: SourceInspectRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        metadata = inspect_rclone_source(payload.source_path)
        preview = _store().create_preview(
            source_path=metadata.source_path,
            source_name=metadata.name,
            size_bytes=metadata.size_bytes,
            modified_at=metadata.modified_at,
            mime_type=metadata.mime_type,
            actor=actor,
        )
    except SourceInspectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "preview_id": preview["id"],
        "name": preview["source_name"],
        "size_bytes": preview["size_bytes"],
        "modified_at": preview["modified_at"],
        "expires_at": preview["expires_at"],
        "preflight_required": True,
        "paid_operation_started": False,
    }


@app.post("/api/v1/batches", status_code=201)
def create_batch(
    payload: CreateBatchRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        result = _store().create_preflight_batch(
            batch_preview_id=payload.batch_preview_id,
            language_code=payload.language_code,
            profile=payload.profile,
            enable_gemini_correction=payload.enable_gemini_correction,
            enable_subtitles=payload.enable_subtitles,
            require_human_review=payload.require_human_review,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    batch = result["batch"]
    return {
        "batch_id": batch["id"],
        "status": batch["status"],
        "item_count": batch["item_count"],
        "job_ids": [job["id"] for job in result["jobs"]],
        "created_at": batch["created_at"],
        "paid_operation_started": False,
        "next_action": "等待各檔案本機 preflight 取得音訊長度與批次預估費用",
    }


@app.get("/api/v1/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    try:
        batch = _store().get_batch(batch_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": batch["id"],
        "name": batch["name"],
        "status": batch["status"],
        "selection_mode": batch["selection_mode"],
        "source_root": batch["source_root"],
        "item_count": batch["item_count"],
        "completed_count": batch["completed_count"],
        "failed_count": batch["failed_count"],
        "estimated_cost_usd": batch["estimated_cost_usd"],
        "reserved_cost_usd": batch["reserved_cost_usd"],
        "actual_cost_usd": batch["actual_cost_usd"],
        "created_at": batch["created_at"],
        "updated_at": batch["updated_at"],
        "revision": batch["revision"],
        "total_duration_seconds": sum(
            float(job["duration_seconds"] or 0) for job in batch["jobs"]
        ),
        "jobs": [_database_job_summary(job) for job in batch["jobs"]],
    }


@app.post("/api/v1/batches/{batch_id}/approve")
def approve_batch(
    batch_id: str,
    payload: ApproveBatchRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        batch = _store().approve_batch(
            batch_id=batch_id,
            expected_revision=payload.expected_revision,
            confirmed_estimated_cost_usd=payload.confirmed_estimated_cost_usd,
            project_limit_usd=_cost_config().project_limit_usd,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "batch_id": batch["id"],
        "status": batch["status"],
        "reserved_cost_usd": batch["reserved_cost_usd"],
        "updated_at": batch["updated_at"],
        "paid_operation_authorized": True,
        "paid_operation_started": False,
    }


@app.post("/api/v1/jobs", status_code=201)
def create_job(payload: CreateJobRequest, request: Request) -> dict[str, Any]:
    actor = _mutation_actor(request)
    try:
        record = _store().create_preflight_job(
            preview_id=payload.preview_id,
            language_code=payload.language_code,
            profile=payload.profile,
            enable_gemini_correction=payload.enable_gemini_correction,
            enable_subtitles=payload.enable_subtitles,
            require_human_review=payload.require_human_review,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job_id": record["id"],
        "status": record["status"],
        "created_at": record["created_at"],
        "paid_operation_started": False,
        "next_action": "等待本機 preflight 取得音訊長度與預估費用",
    }


@app.post("/api/v1/jobs/{job_id}/approve")
def approve_job(
    job_id: str,
    payload: ApproveJobRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _mutation_actor(request)
    config = _cost_config()
    try:
        record = _store().approve_job(
            job_id=job_id,
            expected_revision=payload.expected_revision,
            confirmed_estimated_cost_usd=payload.confirmed_estimated_cost_usd,
            project_limit_usd=config.project_limit_usd,
            actor=actor,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job_id": record["id"],
        "status": record["status"],
        "reserved_cost_usd": record["reserved_cost_usd"],
        "updated_at": record["updated_at"],
    }
