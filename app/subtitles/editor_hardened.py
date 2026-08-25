"""Hardened subtitle routes for strict import and race-free publication history."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.jobs.delivery_state import record_delivery_success
from app.jobs.drive_lock import drive_publish_lock
from app.jobs.drive_publish import publish_outputs, source_parent_destination
from app.subtitles import editor as base

router = APIRouter(tags=["subtitles"])
_REPLACED = {
    "/api/v1/subtitles/import",
    "/api/v1/subtitles/{subtitle_id}/publish",
}
for route in base.router.routes:
    if getattr(route, "path", None) not in _REPLACED:
        router.routes.append(route)


def parse_srt_strict(text: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = [
        block
        for block in base.re.split(r"\n\s*\n", normalized)
        if block.strip()
    ]
    segments: list[dict[str, Any]] = []
    invalid_blocks: list[int] = []
    previous_end = -1
    for block_number, block in enumerate(blocks, 1):
        lines = [line.strip("\ufeff") for line in block.splitlines()]
        if len(lines) < 2:
            invalid_blocks.append(block_number)
            continue
        timing_index = 1 if lines[0].strip().isdigit() else 0
        if timing_index >= len(lines):
            invalid_blocks.append(block_number)
            continue
        match = base.SRT_TIMING.match(lines[timing_index].strip())
        content = "\n".join(lines[timing_index + 1 :]).strip()
        if not match or not content:
            invalid_blocks.append(block_number)
            continue
        values = [int(value) for value in match.groups()]
        if any(values[index] >= 60 for index in (1, 2, 5, 6)):
            invalid_blocks.append(block_number)
            continue
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        if end <= start or start < previous_end:
            invalid_blocks.append(block_number)
            continue
        previous_end = end
        segment_id = str(len(segments) + 1)
        segments.append(
            {
                "segment_id": segment_id,
                "start_ms": start,
                "end_ms": end,
                "raw_text": content,
                "corrected_text": content,
                "text": content,
                "uncertain_terms": [],
            }
        )
    if invalid_blocks:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "SRT contains invalid or overlapping cues; "
                    "no partial import was performed"
                ),
                "total_blocks": len(blocks),
                "valid_blocks": len(segments),
                "invalid_blocks": invalid_blocks[:100],
                "invalid_count": len(invalid_blocks),
            },
        )
    if not segments:
        raise HTTPException(status_code=422, detail="No valid SRT cues found")
    return segments, {
        "total_blocks": len(blocks),
        "valid_blocks": len(segments),
        "invalid_count": 0,
    }


def _update_pipeline_manifests(
    directory: Path,
    *,
    status: str,
    revision: int,
    error: str | None = None,
) -> None:
    for name in ("pipeline-manifest.json", "processing_manifest.json"):
        path = directory / name
        payload = base._read_json(path, {})
        if not isinstance(payload, dict) or not payload:
            continue
        payload["drive_publication_status"] = status
        payload["drive_publication_error"] = error
        payload["editor_published_revision"] = revision
        payload["drive_delivery_updated_at"] = base._iso()
        base._atomic_json(path, payload)


def _delivery_marker(directory: Path) -> dict[str, Any]:
    payload = base._read_json(directory / "drive-delivery-state.json", {})
    return payload if isinstance(payload, dict) else {}


def _marker_revision(marker: dict[str, Any]) -> int:
    try:
        return int(marker.get("editor_revision", -1))
    except (TypeError, ValueError):
        return -1


def _mark_editor_intent(
    directory: Path,
    *,
    revision: int,
    actor: str,
    publication_key: str | None = None,
) -> None:
    payload = {
        "status": "editor_publish_in_progress",
        "intent_created_at": base._iso(),
        "editor_revision": revision,
        "actor": actor,
        "next_attempt_at": None,
    }
    if publication_key is not None:
        payload["publication_key"] = publication_key
    base._atomic_json(directory / "drive-delivery-state.json", payload)
    _update_pipeline_manifests(
        directory,
        status="editor_publish_in_progress",
        revision=revision,
    )


def _mark_editor_failed(
    directory: Path,
    *,
    revision: int,
    actor: str,
    error_type: str,
) -> None:
    base._atomic_json(
        directory / "drive-delivery-state.json",
        {
            "status": "editor_publish_failed",
            "failed_at": base._iso(),
            "editor_revision": revision,
            "actor": actor,
            "error_type": error_type,
            "next_attempt_at": None,
        },
    )
    _update_pipeline_manifests(
        directory,
        status="editor_publish_failed",
        revision=revision,
        error=error_type,
    )


def _mark_pipeline_delivery_superseded(
    directory: Path,
    *,
    revision: int,
    actor: str,
) -> None:
    base._atomic_json(
        directory / "drive-delivery-state.json",
        {
            "status": "superseded_by_editor",
            "superseded_at": base._iso(),
            "editor_revision": revision,
            "actor": actor,
            "next_attempt_at": None,
        },
    )
    _update_pipeline_manifests(
        directory,
        status="superseded_by_editor",
        revision=revision,
    )


@router.post("/api/v1/subtitles/import", status_code=201)
def import_srt(payload: base.ImportSrtRequest, request: Request) -> dict[str, Any]:
    actor = base._mutation_actor(request)
    segments, stats = parse_srt_strict(payload.srt_text)
    subtitle_id = f"import-{base.uuid.uuid4().hex[:12]}"
    directory = base.IMPORTED_DIR / subtitle_id
    directory.mkdir(parents=True, exist_ok=False)
    base._atomic_json(
        directory / "subtitles.json",
        {"source": "external_srt_import", "segments": segments},
    )
    base._atomic_json(
        directory / "metadata.json",
        {
            "name": payload.name,
            "imported_by": actor,
            "imported_at": base._iso(),
            "parse_stats": stats,
        },
    )
    base._save_state(directory, base._edit_state(directory))
    return {
        **base._summary(subtitle_id, directory, "imported"),
        "parse_stats": stats,
    }


@router.post("/api/v1/subtitles/{subtitle_id}/publish")
def publish_edited(
    subtitle_id: str,
    payload: base.PublishEditedRequest,
    request: Request,
) -> dict[str, Any]:
    actor = base._mutation_actor(request)
    directory, kind = base._directory(subtitle_id)
    if kind != "job":
        raise HTTPException(
            status_code=409,
            detail="Imported subtitle has no original Drive destination",
        )
    record = base._job_record(subtitle_id)
    if not record or not str(record.get("source_path", "")).startswith("gdrive:"):
        raise HTTPException(
            status_code=409,
            detail="Original Drive source is unavailable",
        )
    if str(record.get("status") or "") not in {"completed", "awaiting_review"}:
        raise HTTPException(
            status_code=409,
            detail="Transcription must complete before edited subtitles can be published",
        )

    with base._LOCK:
        from app.subtitles import canonical_state

        # Canonical publication identity: the published version is named by
        # source + that source's revision + content hash, never by a bare
        # editor integer. With an Active AI revision this publishes its cues.
        identity = canonical_state.publication_identity(directory)
        segments, state = base._current_segments(directory)
        snapshot_revision = int(state["revision"])
        expected_key = getattr(payload, "expected_publication_key", None)
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
            and (snapshot_revision != payload.expected_revision or snapshot_revision < 1)
        ):
            raise HTTPException(
                status_code=409,
                detail="Edited subtitle revision changed or has no edits",
            )
        rendered = base.render_canonical(directory, segments, snapshot_revision)
        publish_dir = directory / "editor-publish" / f"canonical-{identity['publication_key'].replace(':', '-')}"
        publish_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rendered["srt"], publish_dir / "subtitles-corrected.srt")
        shutil.copy2(rendered["txt"], publish_dir / "transcript-corrected.txt")

    source_path = str(record["source_path"])
    from app.subtitles import canonical_state as _cs

    publication_key = identity["publication_key"]
    with drive_publish_lock(base.DATA_DIR, source_path):
        # Idempotency: the same canonical publication_key must never produce a
        # second Drive write or a duplicate publication event.
        marker = _delivery_marker(directory)
        if (
            str(marker.get("publication_key") or "") == publication_key
            and str(marker.get("status")) in {"completed", "superseded_by_editor"}
        ):
            return {
                "status": "completed",
                "publication_key": publication_key,
                "canonical_source": identity["canonical_source"],
                "canonical_revision": identity["canonical_revision"],
                "published_revision": snapshot_revision,
                "current_revision": snapshot_revision,
                "revision_changed_during_publish": False,
                "zero_edit_review": False,
                "backup_count": int(marker.get("backup_count", 0)),
                "files": {},
                "idempotent_replay": True,
            }
        # Revalidate after acquiring the cross-process Drive lock. A newer edit
        # or already-published editor revision must never be overwritten by an
        # older request that waited longer for this lock.
        with base._LOCK:
            latest_identity = _cs.publication_identity(directory)
            latest_revision = int(base._edit_state(directory)["revision"])
        if latest_identity["publication_key"] != publication_key:
            raise HTTPException(
                status_code=409,
                detail="A newer canonical version exists; reload before publishing",
            )

        # Persist user intent before any remote mutation. If the API process
        # terminates after Drive replacement, the delivery worker sees this
        # marker and cannot restore older pipeline artifacts.
        _mark_editor_intent(
            directory,
            revision=snapshot_revision,
            actor=actor,
            publication_key=publication_key,
        )
        try:
            result = publish_outputs(
                publish_dir,
                source_name=str(record["source_name"]),
                destination=source_parent_destination(source_path),
                output_formats=payload.output_formats,
                authorized=True,
            )
        except Exception as exc:
            _mark_editor_failed(
                directory,
                revision=snapshot_revision,
                actor=actor,
                error_type=type(exc).__name__,
            )
            raise

        _mark_pipeline_delivery_superseded(
            directory,
            revision=snapshot_revision,
            actor=actor,
        )
        record_delivery_success(
            base.DATA_DIR / "course-transcript.db",
            job_id=subtitle_id,
            actor=actor,
            source="editor",
            backup_count=int(result.get("backup_count", 0)),
            published_revision=snapshot_revision,
            publication_key=publication_key,
        )

    with base._LOCK:
        latest = base._edit_state(directory)
        current_revision = int(latest["revision"])
        if not any(
            item.get("type") == "drive_publish"
            and item.get("publication_key") == publication_key
            for item in latest["history"]
            if isinstance(item, dict)
        ):
            latest["history"].append(
                {
                    "revision": current_revision,
                    "published_snapshot_revision": snapshot_revision,
                    "publication_key": publication_key,
                    "canonical_source": identity["canonical_source"],
                    "canonical_revision": identity["canonical_revision"],
                    "type": "drive_publish",
                    "actor": actor,
                    "created_at": base._iso(),
                    "output_formats": payload.output_formats,
                    "backup_count": result.get("backup_count", 0),
                    "revision_changed_during_publish": (
                        current_revision != snapshot_revision
                    ),
                }
            )
            base._save_state(directory, latest)

    return {
        "status": result.get("status"),
        "publication_key": publication_key,
        "canonical_source": identity["canonical_source"],
        "canonical_revision": identity["canonical_revision"],
        "published_revision": snapshot_revision,
        "current_revision": current_revision,
        "revision_changed_during_publish": current_revision != snapshot_revision,
        "backup_count": result.get("backup_count", 0),
        "files": result.get("files", {}),
    }
