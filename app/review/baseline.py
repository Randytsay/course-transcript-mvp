"""Lazy immutable baseline preservation for the owner review API.

M0 keeps `original_text` immutable. Before the first M3 owner mutation for a
video, this module freezes those imported originals as version 1 so rollback can
reach the exact pre-review state even when M3 is introduced after M2 import.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .admin_store import ReviewAdminStore, render_srt
from .store import ReviewNotFound


SYSTEM_ACTOR = "system:import-baseline"


def ensure_import_baseline(
    store: ReviewAdminStore,
    *,
    youtube_video_id: str,
    triggered_by: str,
) -> dict[str, Any]:
    """Create version 1 from immutable imported text if the video has no versions."""
    with store.transaction() as connection:
        existing = connection.execute(
            """
            SELECT * FROM review_subtitle_versions
            WHERE youtube_video_id = ? ORDER BY version_number ASC LIMIT 1
            """,
            (youtube_video_id,),
        ).fetchone()
        if existing is not None:
            return dict(existing)

        rows = connection.execute(
            """
            SELECT id, segment_index, start_ms, end_ms, original_text
            FROM review_subtitle_segments
            WHERE youtube_video_id = ? ORDER BY segment_index
            """,
            (youtube_video_id,),
        ).fetchall()
        if not rows:
            raise ReviewNotFound("Video has no review subtitle segments")

        segments = [
            {
                "id": int(row["id"]),
                "segment_index": int(row["segment_index"]),
                "start_ms": int(row["start_ms"]),
                "end_ms": int(row["end_ms"]),
                "working_text": str(row["original_text"]),
                "revision": 0,
            }
            for row in rows
        ]
        srt_text = render_srt(segments)
        content_sha256 = hashlib.sha256(srt_text.encode("utf-8")).hexdigest()
        version_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO review_subtitle_versions(
                id, youtube_video_id, version_number, parent_version_id,
                source, source_ref, snapshot_json, srt_text, content_sha256,
                created_by_actor, created_at
            ) VALUES (?, ?, 1, NULL, 'import_baseline', NULL, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                youtube_video_id,
                json.dumps(segments, ensure_ascii=False, sort_keys=True),
                srt_text,
                content_sha256,
                SYSTEM_ACTOR,
                now,
            ),
        )
        store._audit(
            connection,
            actor=SYSTEM_ACTOR,
            action="import_baseline_created",
            entity_type="subtitle_version",
            entity_id=version_id,
            payload={
                "youtube_video_id": youtube_video_id,
                "version_number": 1,
                "content_sha256": content_sha256,
                "triggered_by": triggered_by,
            },
        )
        row = connection.execute(
            "SELECT * FROM review_subtitle_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
    return dict(row)


def ensure_suggestion_baseline(
    store: ReviewAdminStore,
    *,
    suggestion_id: str,
    triggered_by: str,
) -> dict[str, Any]:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT seg.youtube_video_id
            FROM review_suggestions s
            JOIN review_subtitle_segments seg ON seg.id = s.segment_id
            WHERE s.id = ?
            """,
            (suggestion_id,),
        ).fetchone()
    if row is None:
        raise ReviewNotFound("Suggestion not found")
    return ensure_import_baseline(
        store,
        youtube_video_id=str(row["youtube_video_id"]),
        triggered_by=triggered_by,
    )


def ensure_batch_baselines(
    store: ReviewAdminStore,
    *,
    batch_id: str,
    item_ids: list[int] | None,
    triggered_by: str,
) -> list[dict[str, Any]]:
    params: list[Any] = [batch_id]
    item_filter = ""
    if item_ids is not None:
        selected = sorted(set(int(item) for item in item_ids))
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        item_filter = f" AND id IN ({placeholders})"
        params.extend(selected)
    with store.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT youtube_video_id
            FROM review_batch_replacement_items
            WHERE batch_id = ? AND status = 'pending' {item_filter}
            ORDER BY youtube_video_id
            """,
            tuple(params),
        ).fetchall()
    return [
        ensure_import_baseline(
            store,
            youtube_video_id=str(row["youtube_video_id"]),
            triggered_by=triggered_by,
        )
        for row in rows
    ]
