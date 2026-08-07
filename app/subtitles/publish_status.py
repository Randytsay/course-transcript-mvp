"""Read-only reconciliation for long-running editor publications.

The publish endpoint can outlive an HTTP proxy connection while Drive backup,
promotion, and verification continue.  This module derives a fail-closed,
revision-aware status without mutating SQLite, job artifacts, Drive, or provider
state.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.subtitles import editor as base


PublishState = Literal["idle", "publishing", "completed", "failed", "ambiguous"]


class PublishStatusResponse(BaseModel):
    status: PublishState
    job_status: str | None
    batch_status: str | None
    current_revision: int
    published_revision: int | None
    revision_changed_during_publish: bool
    zero_edit_review: bool
    drive_publish_status: str | None
    editor_publish_event_count: int
    total_editor_publish_event_count: int
    can_publish: bool
    can_retry: bool
    message: str


def _read_json_state(path: Path) -> tuple[Literal["missing", "valid", "invalid"], Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "invalid", None
    try:
        return "valid", json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "invalid", None


def _read_database(
    database: Path,
    subtitle_id: str,
) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]], bool]:
    """Read job, batch, and editor events through a query-only SQLite handle."""

    if not database.is_file():
        return None, None, [], True

    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        job_row = connection.execute(
            """
            SELECT id, batch_id, source_path, status
            FROM jobs
            WHERE id = ?
            """,
            (subtitle_id,),
        ).fetchone()
        if job_row is None:
            return None, None, [], False

        batch_status: str | None = None
        if job_row["batch_id"]:
            batch_row = connection.execute(
                "SELECT status FROM batches WHERE id = ?",
                (str(job_row["batch_id"]),),
            ).fetchone()
            if batch_row is None:
                return dict(job_row), None, [], True
            batch_status = str(batch_row["status"])

        rows = connection.execute(
            """
            SELECT payload_json
            FROM job_events
            WHERE job_id = ? AND event_type = 'job_drive_editor_published'
            ORDER BY id
            """,
            (subtitle_id,),
        ).fetchall()
    except (sqlite3.Error, OSError):
        return None, None, [], True
    finally:
        try:
            connection.close()
        except (UnboundLocalError, sqlite3.Error):
            pass

    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return dict(job_row), batch_status, [], True
        if not isinstance(payload, dict):
            return dict(job_row), batch_status, [], True
        revision = payload.get("published_revision")
        if isinstance(revision, bool):
            return dict(job_row), batch_status, [], True
        try:
            parsed_revision = int(revision)
        except (TypeError, ValueError):
            return dict(job_row), batch_status, [], True
        events.append({**payload, "published_revision": parsed_revision})
    return dict(job_row), batch_status, events, False


def _editor_state(directory: Path) -> tuple[int, list[dict[str, Any]], bool]:
    state_kind, payload = _read_json_state(directory / "subtitle-editor.json")
    if state_kind == "missing":
        return 0, [], False
    if state_kind == "invalid" or not isinstance(payload, dict):
        return 0, [], True

    revision = payload.get("revision", 0)
    if isinstance(revision, bool):
        return 0, [], True
    try:
        current_revision = int(revision)
    except (TypeError, ValueError):
        return 0, [], True
    if current_revision < 0:
        return 0, [], True

    history = payload.get("history", [])
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        return current_revision, [], True
    return current_revision, list(history), False


def _marker_state(directory: Path) -> tuple[dict[str, Any] | None, bool]:
    kind, payload = _read_json_state(directory / "drive-delivery-state.json")
    if kind == "missing":
        return None, False
    if kind == "invalid" or not isinstance(payload, dict):
        return None, True
    return payload, False


def _drive_state(
    directory: Path,
    revision: int,
) -> tuple[dict[str, Any] | None, bool, bool]:
    publish_dir = directory / "editor-publish" / f"revision-{revision}"
    kind, payload = _read_json_state(publish_dir / "drive-publish-state.json")
    if kind == "missing":
        local_render_exists = publish_dir.is_dir() and any(
            (publish_dir / name).is_file()
            for name in ("subtitles-corrected.srt", "transcript-corrected.txt")
        )
        return None, False, local_render_exists
    if kind == "invalid" or not isinstance(payload, dict):
        return None, True, True
    return payload, False, True


def _revision(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _history_revisions(history: list[dict[str, Any]]) -> tuple[list[int], bool]:
    revisions: list[int] = []
    for item in history:
        if item.get("type") != "drive_publish":
            continue
        value = item.get("published_snapshot_revision", item.get("revision"))
        parsed = _revision(value)
        if parsed is None:
            return [], True
        revisions.append(parsed)
    return revisions, False


def _drive_completed(state: dict[str, Any] | None) -> bool:
    if not state or state.get("status") != "completed":
        return False
    files = state.get("files")
    if not isinstance(files, dict):
        return False
    return all(
        isinstance(files.get(fmt), dict)
        and files[fmt].get("status") == "completed"
        and files[fmt].get("phase") == "completed"
        for fmt in ("srt", "txt")
    )


def _drive_active(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    top_status = state.get("status")
    if top_status in {"failed", "completed"}:
        return False
    if top_status == "in_progress":
        return True
    files = state.get("files")
    if not isinstance(files, dict):
        return False
    return any(
        isinstance(item, dict)
        and (
            item.get("status") in {"pending", "uploading", "in_progress"}
            or item.get("phase")
            in {
                "checking_final",
                "direct_upload",
                "pending_upload",
                "pending_verified",
                "backup_completed",
                "promoting",
            }
        )
        for item in files.values()
    )


def _response(
    *,
    status: PublishState,
    job_status: str | None,
    batch_status: str | None,
    current_revision: int,
    published_revision: int | None,
    drive_publish_status: str | None,
    current_event_count: int,
    total_event_count: int,
    can_publish: bool,
    message: str,
) -> dict[str, object]:
    return PublishStatusResponse(
        status=status,
        job_status=job_status,
        batch_status=batch_status,
        current_revision=current_revision,
        published_revision=published_revision,
        revision_changed_during_publish=(
            published_revision is not None and published_revision != current_revision
        ),
        zero_edit_review=(
            status == "completed"
            and current_revision == 0
            and published_revision == 0
        ),
        drive_publish_status=drive_publish_status,
        editor_publish_event_count=current_event_count,
        total_editor_publish_event_count=total_event_count,
        can_publish=can_publish,
        # This phase never infers that a remote transaction is safe to retry.
        can_retry=False,
        message=message,
    ).model_dump()


def get_publish_status(subtitle_id: str) -> dict[str, object]:
    """Return a revision-aware publication status without side effects."""

    directory, kind = base._directory(subtitle_id)
    current_revision, history, editor_invalid = _editor_state(directory)
    marker, marker_invalid = _marker_state(directory)
    drive_state, drive_invalid, local_render_exists = _drive_state(
        directory,
        current_revision,
    )
    record, batch_status, events, database_invalid = _read_database(
        base.DATA_DIR / "course-transcript.db",
        subtitle_id,
    )

    job_status = str(record.get("status")) if record else None
    source_is_drive = bool(
        record and str(record.get("source_path") or "").startswith("gdrive:")
    )
    event_revisions = [int(item["published_revision"]) for item in events]
    total_event_count = len(event_revisions)
    current_event_count = event_revisions.count(current_revision)
    published_revision = event_revisions[-1] if event_revisions else None
    history_revisions, history_invalid = _history_revisions(history)
    marker_revision = _revision(marker.get("editor_revision")) if marker else None
    marker_status = str(marker.get("status") or "") if marker else None
    drive_status = str(drive_state.get("status") or "") if drive_state else None

    invalid = any(
        (
            editor_invalid,
            marker_invalid,
            drive_invalid,
            database_invalid,
            history_invalid,
            kind != "job",
            record is None,
            bool(marker and marker_revision is None),
            any(revision > current_revision for revision in event_revisions),
            any(revision > current_revision for revision in history_revisions),
            marker_revision is not None and marker_revision > current_revision,
            current_event_count > 1,
        )
    )
    if invalid:
        return _response(
            status="ambiguous",
            job_status=job_status,
            batch_status=batch_status,
            current_revision=current_revision,
            published_revision=published_revision,
            drive_publish_status=drive_status,
            current_event_count=current_event_count,
            total_event_count=total_event_count,
            can_publish=False,
            message="發布狀態資料不一致，請勿重複送出。",
        )

    marker_is_current = marker_revision == current_revision
    current_history_count = history_revisions.count(current_revision)
    drive_is_complete = _drive_completed(drive_state)
    drive_is_active = _drive_active(drive_state)
    batch_complete = batch_status in {None, "completed", "awaiting_review"}

    completed = all(
        (
            source_is_drive,
            job_status == "completed",
            batch_complete,
            current_event_count == 1,
            current_history_count == 1,
            marker_is_current,
            marker_status in {"completed", "superseded_by_editor"},
            drive_is_complete,
        )
    )
    if completed:
        return _response(
            status="completed",
            job_status=job_status,
            batch_status=batch_status,
            current_revision=current_revision,
            published_revision=current_revision,
            drive_publish_status=drive_status,
            current_event_count=current_event_count,
            total_event_count=total_event_count,
            can_publish=False,
            message="字幕已成功發布至 Google Drive。",
        )

    explicit_failure = any(
        (
            marker_is_current and marker_status == "editor_publish_failed",
            drive_state is not None and drive_status == "failed",
        )
    )
    contradictory_failure = explicit_failure and any(
        (
            current_event_count > 0,
            current_history_count > 0,
            marker_is_current and marker_status == "superseded_by_editor",
            drive_is_complete,
        )
    )
    if contradictory_failure:
        return _response(
            status="ambiguous",
            job_status=job_status,
            batch_status=batch_status,
            current_revision=current_revision,
            published_revision=published_revision,
            drive_publish_status=drive_status,
            current_event_count=current_event_count,
            total_event_count=total_event_count,
            can_publish=False,
            message="發布成功與失敗證據互相矛盾，請勿重複送出。",
        )
    if explicit_failure:
        return _response(
            status="failed",
            job_status=job_status,
            batch_status=batch_status,
            current_revision=current_revision,
            published_revision=published_revision,
            drive_publish_status=drive_status,
            current_event_count=current_event_count,
            total_event_count=total_event_count,
            can_publish=False,
            message="發布已記錄失敗；目前不能安全重試。",
        )

    current_activity = any(
        (
            marker_is_current and marker_status == "editor_publish_in_progress",
            drive_is_active,
            local_render_exists
            and marker is None
            and drive_state is None
            and current_event_count == 0,
        )
    )
    if current_activity:
        return _response(
            status="publishing",
            job_status=job_status,
            batch_status=batch_status,
            current_revision=current_revision,
            published_revision=published_revision,
            drive_publish_status=drive_status,
            current_event_count=current_event_count,
            total_event_count=total_event_count,
            can_publish=False,
            message="正在發布至 Google Drive，請勿重複操作。",
        )

    has_current_terminal_evidence = any(
        (
            current_event_count > 0,
            current_history_count > 0,
            marker_is_current,
            drive_state is not None,
        )
    )
    if has_current_terminal_evidence:
        return _response(
            status="ambiguous",
            job_status=job_status,
            batch_status=batch_status,
            current_revision=current_revision,
            published_revision=published_revision,
            drive_publish_status=drive_status,
            current_event_count=current_event_count,
            total_event_count=total_event_count,
            can_publish=False,
            message="發布證據尚未一致，請勿重複送出。",
        )

    can_publish = bool(
        source_is_drive
        and job_status in {"awaiting_review", "completed"}
        and (
            published_revision is None
            or published_revision < current_revision
        )
    )
    return _response(
        status="idle",
        job_status=job_status,
        batch_status=batch_status,
        current_revision=current_revision,
        published_revision=published_revision,
        drive_publish_status=drive_status,
        current_event_count=current_event_count,
        total_event_count=total_event_count,
        can_publish=can_publish,
        message=(
            "可發布目前版本。"
            if can_publish
            else "目前版本不可發布。"
        ),
    )
