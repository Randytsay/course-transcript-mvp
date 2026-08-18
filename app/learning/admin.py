"""Owner/admin APIs for version-grounded AI learning content."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.api import _mutation_actor
from app.review.store import ReviewConflict, ReviewNotFound

from .generator import LearningGenerationError, generate_study_pack
from .source import LearningSourceStore
from .store import LearningStore

router = APIRouter(prefix="/api/v1/review-admin/learning", tags=["learning-admin"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
_store_cache: tuple[Path, LearningStore] | None = None
_source_cache: tuple[Path, LearningSourceStore] | None = None


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class GenerateRequest(ConfirmRequest):
    force: bool = False


def _database_path() -> Path:
    return DATA_DIR / "course-transcript.db"


def _store() -> LearningStore:
    global _store_cache
    path = _database_path()
    if _store_cache is None or _store_cache[0] != path:
        _store_cache = (path, LearningStore(path))
    return _store_cache[1]


def _source_store() -> LearningSourceStore:
    global _source_cache
    path = _database_path()
    if _source_cache is None or _source_cache[0] != path:
        _source_cache = (path, LearningSourceStore(path))
    return _source_cache[1]


def _read_actor(request: Request) -> str:
    require_access = os.environ.get(
        "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS", "false"
    ).lower() in {"1", "true", "yes"}
    actor = request.headers.get("Cf-Access-Authenticated-User-Email")
    assertion = request.headers.get("Cf-Access-Jwt-Assertion")
    if require_access and (not actor or not assertion):
        raise HTTPException(status_code=401, detail="Cloudflare Access identity required")
    return actor or "local-development"


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, ReviewNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReviewConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ValueError, LearningGenerationError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Learning admin operation failed")


@router.get("/overview")
def overview(request: Request) -> dict[str, Any]:
    _read_actor(request)
    store = _store()
    source_store = _source_store()
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT v.youtube_video_id, v.title, v.duration_ms,
                   rv.id AS latest_version_id,
                   rv.version_number AS latest_version_number,
                   rv.content_sha256 AS latest_source_sha256,
                   a.id AS artifact_id,
                   a.source_sha256 AS artifact_source_sha256,
                   a.generated_at AS artifact_generated_at,
                   a.model AS artifact_model,
                   a.prompt_version AS artifact_prompt_version
            FROM review_videos v
            LEFT JOIN review_subtitle_versions rv
                ON rv.id = (
                    SELECT rv2.id FROM review_subtitle_versions rv2
                    WHERE rv2.youtube_video_id = v.youtube_video_id
                    ORDER BY rv2.version_number DESC LIMIT 1
                )
            LEFT JOIN learning_artifacts a
                ON a.id = (
                    SELECT a2.id FROM learning_artifacts a2
                    WHERE a2.youtube_video_id = v.youtube_video_id
                      AND a2.artifact_type = 'study_pack'
                    ORDER BY a2.generated_at DESC LIMIT 1
                )
            ORDER BY v.updated_at DESC, v.title
            """
        ).fetchall()
    videos: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        source_status = source_store.status(str(item["youtube_video_id"]))
        source = source_status["source"]
        item["learning_source_version_id"] = source["subtitle_version_id"] if source else None
        item["learning_source_version_number"] = source["version_number"] if source else None
        item["learning_source_sha256"] = source["source_sha256"] if source else None
        item["learning_source_approved_at"] = source["approved_at"] if source else None
        item["learning_source_is_latest"] = source_status["source_is_latest"]
        if item.get("artifact_id"):
            item["artifact_stale"] = (
                not source
                or str(item.get("artifact_source_sha256")) != str(source.get("source_sha256"))
            )
        else:
            item["artifact_stale"] = None
        videos.append(item)
    return {
        "videos": videos,
        "generation_jobs": store.list_generation_jobs(limit=100),
    }


@router.post("/videos/{youtube_video_id}/approve-source")
def approve_source(
    youtube_video_id: str,
    payload: ConfirmRequest,
    request: Request,
) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required before approving a learning source")
    actor = _mutation_actor(request)
    try:
        source = _source_store().approve_latest(
            youtube_video_id=youtube_video_id,
            actor=actor,
        )
        return {"source": source}
    except Exception as exc:
        raise _handle(exc) from exc


@router.get("/videos/{youtube_video_id}/artifact")
def artifact(youtube_video_id: str, request: Request) -> dict[str, Any]:
    _read_actor(request)
    item = _store().artifact_for_video(youtube_video_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Learning artifact not found")
    return {"artifact": item}


@router.post("/videos/{youtube_video_id}/generate")
def generate(
    youtube_video_id: str,
    payload: GenerateRequest,
    request: Request,
) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required before paid AI generation")
    actor = _mutation_actor(request)
    try:
        return generate_study_pack(
            _store(),
            youtube_video_id=youtube_video_id,
            actor=actor,
            force=payload.force,
        )
    except Exception as exc:
        raise _handle(exc) from exc
