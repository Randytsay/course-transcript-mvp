"""Explicit human-reviewed subtitle publication, including revision zero."""
from __future__ import annotations

import shutil
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.jobs.delivery_state import record_delivery_success
from app.jobs.drive_lock import drive_publish_lock
from app.jobs.drive_publish import publish_outputs, source_parent_destination
from app.subtitles import editor as base
from app.subtitles import editor_hardened as hardened


class PublishReviewedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    output_formats: list[Literal["srt", "txt"]] = Field(
        default_factory=lambda: ["srt", "txt"]
    )


def publish_reviewed(
    subtitle_id: str,
    payload: PublishReviewedRequest,
    request: Request,
) -> dict[str, object]:
    """Publish the exact snapshot a human reviewed, even if no edit was needed."""
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
            detail="Transcription must complete before reviewed subtitles can be published",
        )

    with base._LOCK:
        segments, state = base._current_segments(directory)
        snapshot_revision = int(state["revision"])
        if snapshot_revision != payload.expected_revision:
            raise HTTPException(
                status_code=409,
                detail="Reviewed subtitle revision changed; reload before publishing",
            )
        rendered = base._render_current(directory, segments, snapshot_revision)
        publish_dir = directory / "editor-publish" / f"revision-{snapshot_revision}"
        publish_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rendered["srt"], publish_dir / "subtitles-corrected.srt")
        shutil.copy2(rendered["txt"], publish_dir / "transcript-corrected.txt")

    source_path = str(record["source_path"])
    with drive_publish_lock(base.DATA_DIR, source_path):
        with base._LOCK:
            latest_revision = int(base._edit_state(directory)["revision"])
        marker = hardened._delivery_marker(directory)
        prior_editor_revision = hardened._marker_revision(marker)
        if latest_revision != snapshot_revision or prior_editor_revision > snapshot_revision:
            raise HTTPException(
                status_code=409,
                detail="A newer subtitle revision exists; reload before publishing",
            )

        hardened._mark_editor_intent(
            directory,
            revision=snapshot_revision,
            actor=actor,
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
            hardened._mark_editor_failed(
                directory,
                revision=snapshot_revision,
                actor=actor,
                error_type=type(exc).__name__,
            )
            raise

        hardened._mark_pipeline_delivery_superseded(
            directory,
            revision=snapshot_revision,
            actor=actor,
        )
        completed = record_delivery_success(
            base.DATA_DIR / "course-transcript.db",
            job_id=subtitle_id,
            actor=actor,
            source="editor",
            backup_count=int(result.get("backup_count", 0)),
            published_revision=snapshot_revision,
        )

    with base._LOCK:
        latest = base._edit_state(directory)
        current_revision = int(latest["revision"])
        if not any(
            item.get("type") == "drive_publish"
            and item.get("published_snapshot_revision") == snapshot_revision
            for item in latest["history"]
            if isinstance(item, dict)
        ):
            latest["history"].append(
                {
                    "revision": current_revision,
                    "published_snapshot_revision": snapshot_revision,
                    "type": "drive_publish",
                    "actor": actor,
                    "created_at": base._iso(),
                    "output_formats": payload.output_formats,
                    "backup_count": result.get("backup_count", 0),
                    "revision_changed_during_publish": (
                        current_revision != snapshot_revision
                    ),
                    "zero_edit_review": snapshot_revision == 0,
                }
            )
            base._save_state(directory, latest)

    return {
        "status": result.get("status"),
        "job_status": completed.get("status"),
        "published_revision": snapshot_revision,
        "current_revision": current_revision,
        "revision_changed_during_publish": current_revision != snapshot_revision,
        "zero_edit_review": snapshot_revision == 0,
        "backup_count": result.get("backup_count", 0),
        "files": result.get("files", {}),
    }
