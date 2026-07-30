"""Read-only API for the Course Transcript MVP web workspace.

This module intentionally exposes derived job metadata only.  It never reads
the service-account key, rclone configuration, GCS operation details, or an
arbitrary filesystem path supplied by a browser request.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", APP_ROOT / "data"))
JOBS_DIR = DATA_DIR / "jobs"

app = FastAPI(title="Course Transcript MVP API", version="0.1.0")

# The deployed frontend should use same-origin /api through a reverse proxy.
# CORS is restricted to explicit local development origins only.
origins = [item.strip() for item in os.environ.get(
    "COURSE_TRANSCRIPT_CORS_ORIGINS", "http://localhost:3000"
).split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


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
    qa_unsafe = bool(qa and (
        (qa.get("subtitles_initial") or {}).get("min_segment_ms", 0) < 0
        or (qa.get("chirp") or {}).get("coverage_pct", 100) < 99
    ))

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
    words = int((qa or {}).get("chirp", {}).get("total_words", 0) or 0)
    duration_seconds = (qa or {}).get("audio", {}).get("duration_seconds")
    if duration_seconds is None:
        duration_seconds = subtitle.get("total_duration_ms", 0) / 1000
    stages = _pipeline(directory, qa)
    failed = any(item["status"] == "failed" for item in stages)
    review = any(item["status"] == "warning" for item in stages) or (directory / "subtitles.json").exists()
    completed = all(item["status"] == "completed" for item in stages)
    status = "failed" if failed else "completed" if completed else "review" if review else "transcribing"
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
    }


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "api_version": app.version, "jobs_directory_available": JOBS_DIR.is_dir()}


@app.get("/api/v1/jobs")
def list_jobs() -> dict[str, list[dict[str, Any]]]:
    if not JOBS_DIR.exists():
        return {"jobs": []}
    jobs = [_job_summary(path) for path in JOBS_DIR.iterdir() if path.is_dir()]
    jobs.sort(key=lambda job: job["updated_at"], reverse=True)
    return {"jobs": jobs}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _job_summary(_job_dir(job_id))


@app.get("/api/v1/jobs/{job_id}/segments")
def get_segments(job_id: str) -> dict[str, list[dict[str, Any]]]:
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
    directory = _job_dir(job_id)
    terms = _read_json(directory / "review-terms.json", [])
    return {"review_terms": terms if isinstance(terms, list) else []}


@app.get("/api/v1/jobs/{job_id}/artifacts")
def get_artifacts(job_id: str) -> dict[str, list[dict[str, Any]]]:
    directory = _job_dir(job_id)
    allowed = ["subtitles.srt", "subtitles-corrected.srt", "subtitles.json", "subtitles-corrected.json", "qa-report.json", "qa-report.md", "merged-words.json"]
    artifacts = []
    for name in allowed:
        path = directory / name
        if path.is_file():
            artifacts.append({"id": name, "name": name, "size_bytes": path.stat().st_size, "updated_at": _iso_mtime(path)})
    return {"artifacts": artifacts}
