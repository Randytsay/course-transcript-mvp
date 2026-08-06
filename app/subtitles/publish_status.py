"""Revision-aware and fail-closed publish status determination module."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from app.subtitles import editor as base
from app.subtitles import editor_hardened as hardened

router = APIRouter(tags=["subtitles"])


@router.get("/api/v1/subtitles/{subtitle_id}/publish-status")
def get_publish_status(subtitle_id: str) -> dict[str, Any]:
    try:
        directory, kind = base._directory(subtitle_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Subtitle not found")

    if kind != "job":
        raise HTTPException(
            status_code=409,
            detail="Imported subtitle has no original Drive destination",
        )

    try:
        with base._LOCK:
            segments, state = base._current_segments(directory)
            current_revision = int(state["revision"])
            history = state.get("history") if isinstance(state.get("history"), list) else []
    except HTTPException as exc:
        if exc.status_code == 404:
            raise
        return {
            "status": "ambiguous",
            "job_status": None,
            "batch_status": None,
            "current_revision": 0,
            "published_revision": None,
            "revision_changed_during_publish": False,
            "zero_edit_review": False,
            "drive_publish_status": None,
            "editor_publish_event_count": 0,
            "can_publish": False,
            "can_retry": False,
        }
    except Exception:
        return {
            "status": "ambiguous",
            "job_status": None,
            "batch_status": None,
            "current_revision": 0,
            "published_revision": None,
            "revision_changed_during_publish": False,
            "zero_edit_review": False,
            "drive_publish_status": None,
            "editor_publish_event_count": 0,
            "can_publish": False,
            "can_retry": False,
        }

    marker = hardened._delivery_marker(directory)
    marker_status = marker.get("status") if isinstance(marker, dict) else None
    published_revision = marker.get("editor_revision") if isinstance(marker, dict) else None
    if published_revision is not None:
        try:
            published_revision = int(published_revision)
        except (TypeError, ValueError):
            published_revision = None

    if published_revision is None:
        for item in reversed(history):
            if isinstance(item, dict) and item.get("type") == "drive_publish":
                published_rev_candidate = item.get("published_snapshot_revision", item.get("revision"))
                if published_rev_candidate is not None:
                    try:
                        published_revision = int(published_rev_candidate)
                        break
                    except (TypeError, ValueError):
                        published_revision = None

    database_path = base.DATA_DIR / "course-transcript.db"
    job_status = None
    batch_status = None
    event_count = 0

    if database_path.is_file():
        try:
            db_uri = f"file:{database_path}?mode=ro"
            connection = sqlite3.connect(db_uri, uri=True, timeout=30)
            connection.row_factory = sqlite3.Row
            try:
                job_row = connection.execute(
                    "SELECT status, batch_id FROM jobs WHERE id = ?", (subtitle_id,)
                ).fetchone()
                if job_row:
                    job_status = str(job_row["status"]) if job_row["status"] is not None else None
                    batch_id = job_row["batch_id"]
                    if batch_id:
                        batch_row = connection.execute(
                            "SELECT status FROM batches WHERE id = ?", (batch_id,)
                        ).fetchone()
                        if batch_row and batch_row["status"] is not None:
                            batch_status = str(batch_row["status"])
                event_row = connection.execute(
                    "SELECT COUNT(*) FROM job_events WHERE job_id = ? AND event_type = 'job_drive_editor_published'",
                    (subtitle_id,)
                ).fetchone()
                if event_row:
                    event_count = int(event_row[0])
            finally:
                connection.close()
        except Exception:
            pass

    target_revision_for_publish_state = published_revision if published_revision is not None else current_revision
    drive_publish_status = None
    srt_completed = False
    txt_completed = False
    pending_count = 0
    safe_to_retry = False
    remote_mutation_started = True

    publish_state_path = (
        directory
        / "editor-publish"
        / f"revision-{target_revision_for_publish_state}"
        / "drive-publish-state.json"
    )
    if publish_state_path.is_file():
        publish_state = base._read_json(publish_state_path, {})
        if isinstance(publish_state, dict):
            raw_drive_status = publish_state.get("status")
            if raw_drive_status:
                drive_publish_status = str(raw_drive_status)

            files = publish_state.get("files") if isinstance(publish_state.get("files"), dict) else {}
            srt_info = files.get("srt") if isinstance(files.get("srt"), dict) else {}
            txt_info = files.get("txt") if isinstance(files.get("txt"), dict) else {}
            srt_completed = str(srt_info.get("status")) == "completed"
            txt_completed = str(txt_info.get("status")) == "completed"

            for file_val in files.values():
                if isinstance(file_val, dict) and file_val.get("status") in ("pending", "in_progress"):
                    pending_count += 1

            safe_to_retry = bool(publish_state.get("safe_to_retry", False))
            remote_mutation_started = bool(publish_state.get("remote_mutation_started", True))

    drive_fully_completed = (
        drive_publish_status == "completed"
        and srt_completed
        and txt_completed
        and pending_count == 0
    )

    if not drive_publish_status:
        if marker_status == "superseded_by_editor" and drive_fully_completed:
            drive_publish_status = "completed"
        elif marker_status == "editor_publish_failed":
            drive_publish_status = "failed"
        elif marker_status == "editor_publish_in_progress":
            drive_publish_status = "publishing"

    if marker_status == "editor_publish_in_progress":
        status = "publishing"
    elif marker_status == "editor_publish_failed":
        if event_count > 0 or drive_fully_completed:
            status = "ambiguous"
        else:
            status = "failed"
    elif marker_status == "superseded_by_editor":
        if job_status == "completed" and event_count > 0 and drive_fully_completed:
            status = "completed"
        else:
            status = "ambiguous"
    elif not marker_status:
        if event_count == 0 and not drive_publish_status:
            status = "idle"
        elif job_status == "completed" and event_count > 0 and drive_fully_completed:
            status = "completed"
        else:
            status = "ambiguous"
    else:
        status = "ambiguous"

    zero_edit_review = (published_revision == 0) if published_revision is not None else False
    revision_changed_during_publish = (
        (published_revision != current_revision) if published_revision is not None else False
    )

    can_retry = (
        status == "failed"
        and safe_to_retry is True
        and remote_mutation_started is False
    )

    if status == "idle":
        can_publish = True
    elif status == "completed":
        if published_revision is not None and current_revision > published_revision:
            can_publish = True
        else:
            can_publish = False
    elif status == "failed":
        can_publish = can_retry
    else:
        can_publish = False

    return {
        "status": status,
        "job_status": job_status,
        "batch_status": batch_status,
        "current_revision": current_revision,
        "published_revision": published_revision,
        "revision_changed_during_publish": revision_changed_during_publish,
        "zero_edit_review": zero_edit_review,
        "drive_publish_status": drive_publish_status,
        "editor_publish_event_count": event_count,
        "can_publish": can_publish,
        "can_retry": can_retry,
    }
