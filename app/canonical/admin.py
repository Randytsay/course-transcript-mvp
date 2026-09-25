from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor

from .defaults import DEFAULT_TITLES, SUPPORTED_KEYS
from .store import CanonicalTextStore

router = APIRouter(prefix="/api/v1/review-admin/canonical-texts", tags=["review-admin-canonical-texts"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
_store_cache: tuple[Path, CanonicalTextStore] | None = None


class CanonicalUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=200)
    body_text: str = Field(min_length=1, max_length=200_000)
    note: str = Field(default="", max_length=2000)
    confirm: bool = False


def _store() -> CanonicalTextStore:
    global _store_cache
    path = DATA_DIR / "course-transcript.db"
    if _store_cache is None or _store_cache[0] != path:
        _store_cache = (path, CanonicalTextStore(path))
    return _store_cache[1]


def _read_actor(request: Request) -> str:
    require_access = os.environ.get("COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS", "false").lower() in {
        "1", "true", "yes"
    }
    actor = request.headers.get("Cf-Access-Authenticated-User-Email")
    assertion = request.headers.get("Cf-Access-Jwt-Assertion")
    if require_access and (not actor or not assertion):
        raise HTTPException(status_code=401, detail="Cloudflare Access identity required")
    return actor or "local-development"


@router.get("")
def list_canonical_texts(request: Request) -> dict[str, Any]:
    _read_actor(request)
    return {"documents": _store().list_documents(), "supported_keys": list(SUPPORTED_KEYS)}


@router.get("/{document_key}/versions")
def list_canonical_versions(document_key: str, request: Request) -> dict[str, Any]:
    _read_actor(request)
    try:
        return {"versions": _store().list_versions(document_key)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{document_key}")
def update_canonical_text(
    document_key: str,
    payload: CanonicalUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required")
    if document_key not in SUPPORTED_KEYS:
        raise HTTPException(status_code=404, detail="Unknown canonical document")
    actor = _mutation_actor(request)
    try:
        result = _store().put_version(
            document_key=document_key,
            title=payload.title or DEFAULT_TITLES[document_key],
            body_text=payload.body_text,
            actor=actor,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"document": result}
