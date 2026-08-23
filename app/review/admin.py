"""Cloudflare-Access protected owner workflow for subtitle review decisions."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

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
# AI / Vertex account profile management — owner only
# ---------------------------------------------------------------------------

from .ai_accounts_preflight import run_live_checks  # noqa: E402
from .ai_accounts_store import AIAccountError, AIAccountStore, sanitize  # noqa: E402

AI_ACCOUNTS_DIR = Path(os.environ.get(
    "AI_ACCOUNTS_DIR", "/opt/course-transcript/secrets/ai-accounts"
))
AI_RUNTIME_DIR = Path(os.environ.get(
    "AI_RUNTIME_DIR", "/opt/course-transcript/secrets/ai-runtime"
))


def _ai_accounts_store() -> AIAccountStore:
    return AIAccountStore(
        accounts_dir=AI_ACCOUNTS_DIR,
        runtime_dir=AI_RUNTIME_DIR,
        preflight=run_live_checks,
        audit_callback=None,
    )


class AIAccountAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    sa_json: dict[str, Any]
    location: str = Field(default="global", max_length=64)
    gcs_bucket: str = Field(default="", max_length=222)
    credit_type: str = Field(default="unknown", max_length=32)
    billing_label: str = Field(default="", max_length=128)
    credit_note: str = Field(default="", max_length=512)
    credit_status: str = Field(default="unknown", max_length=16)
    trial_started_at: str = Field(default="", max_length=10)
    trial_expires_at: str = Field(default="", max_length=10)


class AIAccountActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    confirm: bool = False


@router.get("/ai-accounts")
def ai_accounts_list(request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    store = _ai_accounts_store()
    profiles = store.list_profiles()
    for p in profiles:
        p["credit_type_label"] = AIAccountStore.CREDIT_TYPE_LABELS.get(
            p.get("credit_type"), p.get("credit_type"))
        p["credit_note_display"] = p.get("credit_note") or ""
        p["credit_status_is_manual"] = True  # owner-marked, not live billing
        warn = AIAccountStore.trial_warning(p)
        p["trial_warning"] = warn
    profiles = AIAccountStore.sorted_profiles(profiles)
    return sanitize({
        "profiles": profiles,
        "active": store.get_active_name(),
        "previous": store.get_previous(),
        "runtime_status": store.runtime_status(),
        "runtime_verification": store.verify_runtime(),
        "credit_types": list(AIAccountStore.CREDIT_TYPES),
        "credit_statuses": list(AIAccountStore.CREDIT_STATUSES),
        "credit_type_labels": AIAccountStore.CREDIT_TYPE_LABELS,
        "restart_required_hint": "切換後需重啟 api 與 pipeline-worker 容器才會載入新憑證與 Project",
        "billing_note": (
            "Vertex 用量與額度主要跟 GCP Project / Billing 設定相關。"
            "切換帳號會同時切換 Service Account 與目標 GCP Project。"
            "Google AI Pro：每月提供 GenAI & Cloud credit，可套用到指定 Cloud Billing "
            "Account，並可用於 Vertex AI 等 Google Cloud 產品。"
            "Google Cloud Free Trial：新戶 Welcome Credit 通常為 US$300 / 90 天；"
            "實際有效額度與到期時間以 Google Cloud Billing Console 為準。"
            "切換 Profile 會切換 Service Account 與目標 GCP Project；"
            "實際費用與 Credit 由該 Project 所連結的 Cloud Billing Account 決定。"
            "本系統顯示的 credit 狀態為管理員標記，非 Google 即時帳務資料。"
        ),
    })


class AIAccountCreditStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    credit_status: str = Field(max_length=16)


@router.post("/ai-accounts/credit-status")
def ai_accounts_credit_status(payload: AIAccountCreditStatusRequest,
                              request: Request) -> dict[str, Any]:
    """Owner manually marks available/low/exhausted/expired/disabled."""
    actor = _admin_mutation_actor(request)
    store = _ai_accounts_store()
    try:
        result = store.update_credit_status(name=payload.name,
                                            credit_status=payload.credit_status,
                                            actor=actor)
    except AIAccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit_ai_account("ai_profile_credit_status", payload.name,
                      {"previous": result.get("previous"), "new": result["credit_status"]},
                      actor)
    return sanitize(result)


@router.post("/ai-accounts")
def ai_accounts_add(payload: AIAccountAddRequest, request: Request) -> dict[str, Any]:
    actor = _admin_mutation_actor(request)
    store = _ai_accounts_store()
    try:
        result = store.save_profile(
            name=payload.name,
            sa_json=payload.sa_json,
            location=payload.location,
            gcs_bucket=payload.gcs_bucket,
            actor=actor,
            credit_type=payload.credit_type,
            billing_label=payload.billing_label,
            credit_note=payload.credit_note,
            credit_status=payload.credit_status,
            trial_started_at=payload.trial_started_at,
            trial_expires_at=payload.trial_expires_at,
        )
    except AIAccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit_ai_account("ai_profile_replaced" if result["replaced"] else "ai_profile_added",
                      result["name"], {"client_email": result["client_email"],
                                       "project_id": result["project_id"]}, actor)
    return sanitize(result)


@router.post("/ai-accounts/preflight")
def ai_accounts_preflight(payload: AIAccountActionRequest, request: Request) -> dict[str, Any]:
    """Run read-only checks for a candidate profile. Changes nothing."""
    _admin_read_actor(request)
    store = _ai_accounts_store()
    active = store.get_active_name()
    try:
        meta = store.load_metadata(payload.name)
        checks = store.run_preflight(payload.name)
    except AIAccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    same_project = bool(active) and (
        store._safe_meta(active).get("project_id") == meta.get("project_id")
    )
    return {
        "ok": checks.get("ok", False),
        "checks": checks,
        "candidate": sanitize(meta),
        "current": store.get_active_name(),
        "same_project_warning": same_project,
    }


@router.post("/ai-accounts/switch")
def ai_accounts_switch(payload: AIAccountActionRequest, request: Request) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(
            status_code=422,
            detail="切換需要明確 confirm=true（請先執行 preflight 並確認差異）",
        )
    actor = _admin_mutation_actor(request)
    store = _ai_accounts_store()
    try:
        result = store.switch(
            name=payload.name,
            actor=actor,
            confirm=True,
        )
    except AIAccountError as exc:
        status = 404 if "找不到" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    _audit_ai_account("ai_profile_switched", payload.name,
                      {"previous": result.get("previous"),
                       "project_id": result.get("project_id"),
                       "location": result.get("location")}, actor)
    return sanitize(result)


@router.post("/ai-accounts/rollback")
def ai_accounts_rollback(request: Request) -> dict[str, Any]:
    actor = _admin_mutation_actor(request)
    store = _ai_accounts_store()
    try:
        result = store.rollback(actor=actor)
    except AIAccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit_ai_account("ai_profile_rolled_back", str(result.get("name")), {}, actor)
    return sanitize(result)


@router.post("/ai-accounts/delete")
def ai_accounts_delete(payload: AIAccountActionRequest, request: Request) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="刪除需要明確 confirm=true")
    actor = _admin_mutation_actor(request)
    store = _ai_accounts_store()
    try:
        result = store.delete_profile(name=payload.name, actor=actor)
    except AIAccountError as exc:
        status = 404 if "找不到" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    _audit_ai_account("ai_profile_deleted", payload.name, {}, actor)
    return sanitize(result)


def _audit_ai_account(action: str, entity_id: str, payload: dict[str, Any], actor: str) -> None:
    """Write sanitized (secret-free) records to review_admin_audit."""
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
                json.dumps(sanitize(payload), ensure_ascii=False, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )

# ---------------------------------------------------------------------------
# AI model provider profiles (OpenRouter / MiniMax API keys) — owner only
# ---------------------------------------------------------------------------

from app.providers.correction.registry import (  # noqa: E402
    AIProviderProfileStore,
    PROVIDER_CLASSES,
    redact,
)
from app.providers.correction.base import ProviderError  # noqa: E402

AI_PROVIDER_PROFILES_DIR = Path(os.environ.get(
    "AI_PROVIDER_PROFILES_DIR", "/opt/course-transcript/secrets/ai-providers"
))


def _provider_store() -> AIProviderProfileStore:
    return AIProviderProfileStore(AI_PROVIDER_PROFILES_DIR)


class AIProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9-]{1,47}$")
    name: str = Field(min_length=1, max_length=64)
    provider: Literal["openrouter", "minimax"]
    api_key: str = Field(min_length=8, max_length=256)
    default_model: str = Field(min_length=1, max_length=128)


class AIProviderKeyReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=8, max_length=256)


class AIProviderActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


@router.get("/ai-providers")
def ai_providers_list(request: Request) -> dict[str, Any]:
    _admin_read_actor(request)
    store = _provider_store()
    profiles = store.list_profiles()  # redacted; keys never returned
    return {
        "profiles": profiles,
        "supported_providers": ["minimax", "openrouter"],
        "capabilities": {
            "openrouter": {"realtime": True, "batch": True,
                           "batch_note": "使用 OpenRouter 官方 Batch API"},
            "minimax": {"realtime": True, "batch": False,
                        "batch_note": "MiniMax 官方目前未提供批次折扣 API，僅即時模式"},
        },
        "security_note": "API key 只會寫入受保護目錄（0600），不會顯示或回傳。",
    }


@router.post("/ai-providers")
def ai_providers_create(payload: AIProviderCreateRequest, request: Request) -> dict[str, Any]:
    actor = _admin_mutation_actor(request)
    store = _provider_store()
    try:
        result = store.create(profile_id=payload.id, name=payload.name,
                              provider=payload.provider, api_key=payload.api_key,
                              default_model=payload.default_model)
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit_ai_account("ai_provider_profile_created", payload.id,
                      redact({"provider": payload.provider,
                              "default_model": payload.default_model}), actor)
    return redact(result)


@router.post("/ai-providers/{profile_id}/key")
def ai_providers_replace_key(profile_id: str,
                             payload: AIProviderKeyReplaceRequest,
                             request: Request) -> dict[str, Any]:
    actor = _admin_mutation_actor(request)
    store = _provider_store()
    try:
        result = store.replace_key(profile_id, api_key=payload.api_key)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit_ai_account("ai_provider_key_replaced", profile_id, {}, actor)
    return redact(result)


@router.post("/ai-providers/{profile_id}/test")
def ai_providers_test(profile_id: str, request: Request) -> dict[str, Any]:
    """Read-only connection test. NEVER runs a paid generation."""
    _admin_read_actor(request)
    store = _provider_store()
    try:
        client = store.build_client(profile_id)
        result = client.validate_credentials()
        status = "PASS" if result.get("ok") else "FAIL"
        store.mark_validated(profile_id, status)
    except ProviderError as exc:
        store.mark_validated(profile_id, "FAIL")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return redact({"id": profile_id, "validation_status": status,
                   **{k: v for k, v in result.items() if k != "ok"}})


@router.post("/ai-providers/{profile_id}/delete")
def ai_providers_delete(profile_id: str,
                        payload: AIProviderActionRequest,
                        request: Request) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="刪除需要明確 confirm=true")
    actor = _admin_mutation_actor(request)
    store = _provider_store()
    try:
        result = store.delete(profile_id)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit_ai_account("ai_provider_profile_deleted", profile_id, {}, actor)
    return redact(result)
