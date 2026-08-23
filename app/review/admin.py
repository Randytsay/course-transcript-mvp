"""Cloudflare-Access protected owner workflow for subtitle review decisions."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _mutation_actor

from .admin_store import ReviewAdminStore
from .baseline import ensure_batch_baselines, ensure_suggestion_baseline
from .store import ReviewConflict, ReviewNotFound, changed_char_count
from .youtube_publish import YouTubePublishError, publish_caption_version

router = APIRouter(prefix="/api/v1/review-admin", tags=["review-admin"])
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
_store_cache: tuple[Path, ReviewAdminStore] | None = None


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=1000)


class BatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    find_text: str = Field(min_length=1, max_length=500)
    replace_text: str = Field(max_length=500)
    youtube_video_ids: list[str] | None = Field(default=None, max_length=200)


class BatchApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    item_ids: list[int] | None = Field(default=None, max_length=5000)


def _store() -> ReviewAdminStore:
    global _store_cache
    path = DATA_DIR / "course-transcript.db"
    if _store_cache is None or _store_cache[0] != path:
        _store_cache = (path, ReviewAdminStore(path))
    return _store_cache[1]


def _admin_read_actor(request: Request) -> str:
    require_access = os.environ.get(
        "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS", "false"
    ).lower() in {"1", "true", "yes"}
    actor = request.headers.get("Cf-Access-Authenticated-User-Email")
    assertion = request.headers.get("Cf-Access-Jwt-Assertion")
    if require_access and (not actor or not assertion):
        raise HTTPException(status_code=401, detail="Cloudflare Access identity required")
    return actor or "local-development"


def _admin_mutation_actor(request: Request) -> str:
    return _mutation_actor(request)


def _confirmed(value: bool) -> None:
    if not value:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required")


def _handle_admin_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ReviewNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReviewConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Review admin operation failed")


def _version_summary(version: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in version.items()
        if key not in {"snapshot_json", "srt_text", "youtube_response_json"}
    }


def _decorate_suggestions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    ids = [str(item["id"]) for item in rows]
    placeholders = ",".join("?" for _ in ids)
    with _store().connect() as connection:
        audit_rows = connection.execute(
            f"""
            SELECT entity_id, actor, action, payload_json, created_at
            FROM review_admin_audit
            WHERE entity_type = 'suggestion'
              AND action IN ('suggestion_approved', 'suggestion_rejected')
              AND entity_id IN ({placeholders})
            ORDER BY id DESC
            """,
            tuple(ids),
        ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for audit in audit_rows:
        entity_id = str(audit["entity_id"])
        if entity_id in latest:
            continue
        try:
            payload = json.loads(audit["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        latest[entity_id] = {
            "reviewed_by_actor": audit["actor"],
            "review_action": audit["action"],
            "review_payload": payload,
            "review_audit_at": audit["created_at"],
        }
    return [{**item, **latest.get(str(item["id"]), {})} for item in rows]


@router.get("/overview")
def overview(request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    store = _store()
    pending = store.list_suggestions(status="pending", limit=2000)
    versions = store.list_versions(limit=2000)
    conflict_count = sum(bool(item["conflict"]) for item in pending)
    latest_by_video: dict[str, dict[str, Any]] = {}
    for version in versions:
        latest_by_video.setdefault(str(version["youtube_video_id"]), version)
    with store.connect() as connection:
        video_count = int(connection.execute("SELECT COUNT(*) FROM review_videos").fetchone()[0])
        reviewer_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_users WHERE status = 'active'"
            ).fetchone()[0]
        )
    return {
        "pending_suggestions": len(pending),
        "conflicting_suggestions": conflict_count,
        "version_count": len(versions),
        "video_count": video_count,
        "reviewer_count": reviewer_count,
        "published_video_count": len(
            {
                item["youtube_video_id"]
                for item in versions
                if item["publish_status"] == "published"
            }
        ),
        "latest_versions": [
            _version_summary(item) for item in list(latest_by_video.values())[:100]
        ],
    }


@router.get("/suggestions")
def suggestions(
    request: Request,
    status: str = Query(default="pending", pattern=r"^(pending|approved|rejected)$"),
    youtube_video_id: str | None = None,
) -> dict[str, Any]:
    _admin_read_actor(request)
    try:
        rows = _store().list_suggestions(
            status=status,
            youtube_video_id=youtube_video_id,
            limit=2000,
        )
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc
    return {"suggestions": _decorate_suggestions(rows)}


@router.get("/suggestions/{suggestion_id}/context")
def suggestion_context(suggestion_id: str, request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    with _store().connect() as connection:
        row = connection.execute(
            """
            SELECT
                s.id AS suggestion_id,
                seg.youtube_video_id,
                v.title AS video_title,
                seg.segment_index,
                seg.start_ms,
                seg.end_ms,
                seg.working_text AS current_text,
                prev.working_text AS previous_text,
                prev.start_ms AS previous_start_ms,
                next.working_text AS next_text,
                next.start_ms AS next_start_ms
            FROM review_suggestions s
            JOIN review_subtitle_segments seg ON seg.id = s.segment_id
            JOIN review_videos v ON v.youtube_video_id = seg.youtube_video_id
            LEFT JOIN review_subtitle_segments prev
              ON prev.youtube_video_id = seg.youtube_video_id
             AND prev.segment_index = seg.segment_index - 1
            LEFT JOIN review_subtitle_segments next
              ON next.youtube_video_id = seg.youtube_video_id
             AND next.segment_index = seg.segment_index + 1
            WHERE s.id = ?
            """,
            (suggestion_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"context": dict(row)}


@router.get("/audit")
def audit_log(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    _admin_read_actor(request)
    with _store().connect() as connection:
        rows = connection.execute(
            """
            SELECT id, actor, action, entity_type, entity_id, payload_json, created_at
            FROM review_admin_audit
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
            item.pop("payload_json", None)
        items.append(item)
    return {"audit": items}


@router.post("/suggestions/{suggestion_id}/approve")
def approve_suggestion(
    suggestion_id: str,
    payload: ConfirmRequest,
    request: Request,
) -> dict[str, Any]:
    _confirmed(payload.confirm)
    actor = _admin_mutation_actor(request)
    store = _store()
    try:
        ensure_suggestion_baseline(
            store,
            suggestion_id=suggestion_id,
            triggered_by=actor,
        )
        result = store.approve_suggestion(suggestion_id=suggestion_id, actor=actor)
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc
    return {
        "suggestion": result["suggestion"],
        "version": _version_summary(result["version"]),
    }


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(
    suggestion_id: str,
    payload: RejectRequest,
    request: Request,
) -> dict[str, Any]:
    actor = _admin_mutation_actor(request)
    try:
        result = _store().reject_suggestion(
            suggestion_id=suggestion_id,
            actor=actor,
            reason=payload.reason,
        )
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc
    return {"suggestion": result}


@router.post("/batches")
def create_batch(payload: BatchCreateRequest, request: Request) -> dict[str, Any]:
    actor = _admin_mutation_actor(request)
    try:
        return _store().create_batch(
            find_text=payload.find_text,
            replace_text=payload.replace_text,
            actor=actor,
            youtube_video_ids=payload.youtube_video_ids,
        )
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    try:
        return _store().get_batch(batch_id)
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc


@router.post("/batches/{batch_id}/apply")
def apply_batch(
    batch_id: str,
    payload: BatchApplyRequest,
    request: Request,
) -> dict[str, Any]:
    _confirmed(payload.confirm)
    actor = _admin_mutation_actor(request)
    store = _store()
    try:
        ensure_batch_baselines(
            store,
            batch_id=batch_id,
            item_ids=payload.item_ids,
            triggered_by=actor,
        )
        result = store.apply_batch(
            batch_id=batch_id,
            actor=actor,
            item_ids=payload.item_ids,
        )
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc
    return {
        **result,
        "versions": [_version_summary(item) for item in result["versions"]],
    }


@router.get("/versions")
def versions(request: Request, youtube_video_id: str | None = None) -> dict[str, Any]:
    _admin_read_actor(request)
    try:
        rows = _store().list_versions(youtube_video_id=youtube_video_id, limit=2000)
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc
    return {"versions": [_version_summary(item) for item in rows]}


@router.get("/versions/{version_id}")
def version_detail(version_id: str, request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    try:
        return {"version": _store().get_version(version_id)}
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc


@router.get("/versions/{version_id}/publish-preview")
def publish_preview(version_id: str, request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    store = _store()
    try:
        target = store.get_version(version_id)
        video_versions = store.list_versions(
            youtube_video_id=str(target["youtube_video_id"]),
            limit=2000,
        )
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc

    try:
        target_snapshot = json.loads(target["snapshot_json"])
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail="Target version snapshot is invalid") from exc

    published = next(
        (
            item
            for item in video_versions
            if item["publish_status"] == "published" and item["id"] != version_id
        ),
        None,
    )
    reference = published
    if reference is None and target.get("parent_version_id"):
        try:
            reference = store.get_version(str(target["parent_version_id"]))
        except ReviewNotFound:
            reference = None

    changed_segments = 0
    changed_characters = 0
    if reference is not None:
        try:
            reference_snapshot = json.loads(reference["snapshot_json"])
        except json.JSONDecodeError:
            reference_snapshot = []
        before_by_index = {
            int(item["segment_index"]): str(item["working_text"])
            for item in reference_snapshot
            if "segment_index" in item
        }
        for item in target_snapshot:
            index = int(item["segment_index"])
            after = str(item["working_text"])
            before = before_by_index.get(index, "")
            if before != after:
                changed_segments += 1
                changed_characters += changed_char_count(before, after)
    else:
        changed_segments = len(target_snapshot)
        changed_characters = sum(len(str(item.get("working_text", ""))) for item in target_snapshot)

    latest = max(video_versions, key=lambda item: int(item["version_number"])) if video_versions else target
    caption_track_id = str(
        target.get("current_caption_track_id")
        or target.get("youtube_caption_track_id")
        or ""
    ).strip()
    return {
        "version": _version_summary(target),
        "is_latest": str(latest["id"]) == version_id,
        "is_already_published": target["publish_status"] == "published",
        "caption_track_configured": bool(caption_track_id),
        "caption_track_id": caption_track_id or None,
        "reference_version": _version_summary(reference) if reference else None,
        "changed_segments": changed_segments,
        "changed_characters": changed_characters,
        "timing_policy": "fixed",
    }


@router.post("/versions/{version_id}/restore")
def restore_version(
    version_id: str,
    payload: ConfirmRequest,
    request: Request,
) -> dict[str, Any]:
    _confirmed(payload.confirm)
    actor = _admin_mutation_actor(request)
    try:
        result = _store().restore_version(version_id=version_id, actor=actor)
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc
    return {
        **result,
        "version": _version_summary(result["version"]),
    }


@router.post("/versions/{version_id}/publish")
def publish_version(
    version_id: str,
    payload: ConfirmRequest,
    request: Request,
) -> dict[str, Any]:
    _confirmed(payload.confirm)
    actor = _admin_mutation_actor(request)
    store = _store()
    try:
        version = store.get_version(version_id)
    except (ValueError, ReviewNotFound, ReviewConflict) as exc:
        raise _handle_admin_error(exc) from exc

    caption_track_id = str(
        version.get("current_caption_track_id")
        or version.get("youtube_caption_track_id")
        or ""
    ).strip()
    if not caption_track_id:
        raise HTTPException(status_code=409, detail="Video has no imported YouTube caption track ID")
    if version["publish_status"] == "published" and version.get("youtube_caption_track_id") == caption_track_id:
        return {"version": _version_summary(version), "already_published": True}

    try:
        response = publish_caption_version(
            caption_track_id=caption_track_id,
            srt_text=str(version["srt_text"]),
        )
        published = store.mark_publish_success(
            version_id=version_id,
            caption_track_id=caption_track_id,
            actor=actor,
            youtube_response=response,
        )
        return {
            "version": _version_summary(published),
            "already_published": False,
            "youtube": response,
        }
    except YouTubePublishError as exc:
        store.mark_publish_failed(version_id=version_id, actor=actor, error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# AI account (GCP service account) management — owner only
# ---------------------------------------------------------------------------

from .ai_accounts_store import AIAccountError, AIAccountStore  # noqa: E402

AI_ACCOUNTS_DIR = Path(os.environ.get("AI_ACCOUNTS_DIR", "/opt/course-transcript/secrets/ai-accounts"))
AI_ACTIVE_FILE = AI_ACCOUNTS_DIR / ".active"
AI_TARGET_KEY_PATH = Path(
    os.environ.get("GCP_CREDENTIALS_TARGET_PATH", "/opt/course-transcript/secrets/gcp-sa.json")
)


class AIAccountUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    sa_json: dict[str, Any]


class AIAccountActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    confirm: bool = False


def _audit_ai_account(action: str, entity_id: str, payload: dict[str, Any], actor: str) -> None:
    """Write to the same review_admin_audit table as other admin actions."""
    from datetime import UTC, datetime

    with _store().transaction() as connection:
        connection.execute(
            """
            INSERT INTO review_admin_audit(
                actor, action, entity_type, entity_id, payload_json, created_at
            ) VALUES (?, ?, 'ai_account', ?, ?, ?)
            """,
            (
                actor,
                action,
                entity_id,
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )


@router.get("/ai-accounts")
def ai_accounts_list(request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    store = AIAccountStore(AI_ACCOUNTS_DIR, AI_ACTIVE_FILE, AI_TARGET_KEY_PATH)
    return {
        "accounts": store.list_accounts(),
        "active": store.get_active(),
        "verify": store.verify_active(),
        "restart_required_hint": "切換後需重啟 api / pipeline-worker 容器才會載入新憑證",
    }


@router.post("/ai-accounts")
def ai_accounts_add(payload: AIAccountUploadRequest, request: Request) -> dict[str, Any]:
    actor = _admin_mutation_actor(request)
    store = AIAccountStore(AI_ACCOUNTS_DIR, AI_ACTIVE_FILE, AI_TARGET_KEY_PATH)
    try:
        result = store.add_account(name=payload.name, sa_json=payload.sa_json, actor=actor)
    except AIAccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit_ai_account("ai_account_added" if not result["replaced"] else "ai_account_replaced",
                      result["name"], {"client_email": result["client_email"]}, actor)
    return result


@router.post("/ai-accounts/switch")
def ai_accounts_switch(payload: AIAccountActionRequest, request: Request) -> dict[str, Any]:
    _confirmed(payload.confirm)
    actor = _admin_mutation_actor(request)
    store = AIAccountStore(AI_ACCOUNTS_DIR, AI_ACTIVE_FILE, AI_TARGET_KEY_PATH)
    try:
        result = store.switch_active(name=payload.name, actor=actor)
    except AIAccountError as exc:
        raise HTTPException(status_code=409 if "找不到" not in str(exc) else 404, detail=str(exc)) from exc
    _audit_ai_account("ai_account_switched", result["name"],
                      {"previous": result["previous"], "client_email": result["client_email"]}, actor)
    return result


@router.post("/ai-accounts/delete")
def ai_accounts_delete(payload: AIAccountActionRequest, request: Request) -> dict[str, Any]:
    _confirmed(payload.confirm)
    actor = _admin_mutation_actor(request)
    store = AIAccountStore(AI_ACCOUNTS_DIR, AI_ACTIVE_FILE, AI_TARGET_KEY_PATH)
    try:
        result = store.delete_account(name=payload.name, actor=actor)
    except AIAccountError as exc:
        raise HTTPException(status_code=409 if "使用中" in str(exc) else 404, detail=str(exc)) from exc
    _audit_ai_account("ai_account_deleted", payload.name, {}, actor)
    return result
