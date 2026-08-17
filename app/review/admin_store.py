"""Owner review workflow: approvals, batch replacement and immutable subtitle versions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .store import ReviewConflict, ReviewNotFound, ReviewStore


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def _srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def render_srt(segments: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for position, segment in enumerate(segments, 1):
        blocks.append(
            "\n".join(
                [
                    str(position),
                    f"{_srt_time(int(segment['start_ms']))} --> {_srt_time(int(segment['end_ms']))}",
                    str(segment["working_text"]),
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


class ReviewAdminStore:
    """Transactional owner workflow layered on the existing review_* tables."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.review = ReviewStore(self.database_path)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_admin_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_subtitle_versions (
                    id TEXT PRIMARY KEY,
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    version_number INTEGER NOT NULL CHECK(version_number > 0),
                    parent_version_id TEXT REFERENCES review_subtitle_versions(id),
                    source TEXT NOT NULL,
                    source_ref TEXT,
                    snapshot_json TEXT NOT NULL,
                    srt_text TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    created_by_actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    publish_status TEXT NOT NULL DEFAULT 'draft'
                        CHECK(publish_status IN ('draft', 'published', 'superseded', 'publish_failed')),
                    published_at TEXT,
                    youtube_caption_track_id TEXT,
                    youtube_response_json TEXT,
                    publish_error TEXT,
                    UNIQUE(youtube_video_id, version_number)
                );

                CREATE TABLE IF NOT EXISTS review_batch_replacements (
                    id TEXT PRIMARY KEY,
                    find_text TEXT NOT NULL,
                    replace_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft', 'applied', 'cancelled')),
                    created_by_actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                );

                CREATE TABLE IF NOT EXISTS review_batch_replacement_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL REFERENCES review_batch_replacements(id),
                    segment_id INTEGER NOT NULL REFERENCES review_subtitle_segments(id),
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    base_revision INTEGER NOT NULL CHECK(base_revision >= 0),
                    original_text_snapshot TEXT NOT NULL,
                    proposed_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'applied', 'conflict', 'skipped')),
                    applied_version_id TEXT REFERENCES review_subtitle_versions(id),
                    error TEXT,
                    UNIQUE(batch_id, segment_id)
                );

                CREATE INDEX IF NOT EXISTS review_admin_audit_created_idx
                    ON review_admin_audit(created_at DESC);
                CREATE INDEX IF NOT EXISTS review_versions_video_idx
                    ON review_subtitle_versions(youtube_video_id, version_number DESC);
                CREATE INDEX IF NOT EXISTS review_batch_item_batch_idx
                    ON review_batch_replacement_items(batch_id, status, youtube_video_id);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None, message: str) -> dict[str, Any]:
        if row is None:
            raise ReviewNotFound(message)
        return dict(row)

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO review_admin_audit(
                actor, action, entity_type, entity_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor,
                action,
                entity_type,
                entity_id,
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                _iso(),
            ),
        )

    def _snapshot_video(
        self,
        connection: sqlite3.Connection,
        *,
        youtube_video_id: str,
        actor: str,
        source: str,
        source_ref: str | None,
    ) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT id, segment_index, start_ms, end_ms, working_text, revision
            FROM review_subtitle_segments
            WHERE youtube_video_id = ? ORDER BY segment_index
            """,
            (youtube_video_id,),
        ).fetchall()
        if not rows:
            raise ReviewNotFound("Video has no review subtitle segments")
        segments = [dict(row) for row in rows]
        srt = render_srt(segments)
        digest = hashlib.sha256(srt.encode("utf-8")).hexdigest()
        previous = connection.execute(
            """
            SELECT id, version_number FROM review_subtitle_versions
            WHERE youtube_video_id = ? ORDER BY version_number DESC LIMIT 1
            """,
            (youtube_video_id,),
        ).fetchone()
        number = int(previous["version_number"]) + 1 if previous else 1
        version_id = uuid.uuid4().hex
        now = _iso()
        connection.execute(
            """
            INSERT INTO review_subtitle_versions(
                id, youtube_video_id, version_number, parent_version_id,
                source, source_ref, snapshot_json, srt_text, content_sha256,
                created_by_actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                youtube_video_id,
                number,
                previous["id"] if previous else None,
                source,
                source_ref,
                json.dumps(segments, ensure_ascii=False, sort_keys=True),
                srt,
                digest,
                actor,
                now,
            ),
        )
        self._audit(
            connection,
            actor=actor,
            action="version_created",
            entity_type="subtitle_version",
            entity_id=version_id,
            payload={
                "youtube_video_id": youtube_video_id,
                "version_number": number,
                "source": source,
                "source_ref": source_ref,
                "content_sha256": digest,
            },
        )
        return dict(
            connection.execute(
                "SELECT * FROM review_subtitle_versions WHERE id = ?", (version_id,)
            ).fetchone()
        )

    def list_suggestions(
        self,
        *,
        status: str = "pending",
        youtube_video_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError("invalid suggestion status")
        if limit < 1 or limit > 2000:
            raise ValueError("limit must be between 1 and 2000")
        params: list[Any] = [status]
        video_filter = ""
        if youtube_video_id:
            video_filter = " AND seg.youtube_video_id = ?"
            params.append(youtube_video_id)
        params.append(limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    s.*,
                    seg.youtube_video_id,
                    seg.segment_index,
                    seg.start_ms,
                    seg.end_ms,
                    seg.working_text AS current_text,
                    seg.revision AS current_revision,
                    v.title AS video_title,
                    u.display_name AS reviewer_name,
                    u.avatar_url AS reviewer_avatar
                FROM review_suggestions s
                JOIN review_subtitle_segments seg ON seg.id = s.segment_id
                JOIN review_videos v ON v.youtube_video_id = seg.youtube_video_id
                JOIN review_users u ON u.id = s.user_id
                WHERE s.status = ? {video_filter}
                ORDER BY s.created_at ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["conflict"] = (
                int(item["base_segment_revision"]) != int(item["current_revision"])
                or str(item["original_text_snapshot"]) != str(item["current_text"])
            )
            result.append(item)
        return result

    def approve_suggestion(self, *, suggestion_id: str, actor: str) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT s.*, seg.youtube_video_id, seg.working_text, seg.revision
                FROM review_suggestions s
                JOIN review_subtitle_segments seg ON seg.id = s.segment_id
                WHERE s.id = ?
                """,
                (suggestion_id,),
            ).fetchone()
            suggestion = self._row(row, "Suggestion not found")
            if suggestion["status"] != "pending":
                raise ReviewConflict("Suggestion is no longer pending")
            if (
                int(suggestion["base_segment_revision"]) != int(suggestion["revision"])
                or str(suggestion["original_text_snapshot"]) != str(suggestion["working_text"])
            ):
                self._audit(
                    connection,
                    actor=actor,
                    action="suggestion_conflict",
                    entity_type="suggestion",
                    entity_id=suggestion_id,
                    payload={
                        "base_revision": suggestion["base_segment_revision"],
                        "current_revision": suggestion["revision"],
                        "original_text_snapshot": suggestion["original_text_snapshot"],
                        "current_text": suggestion["working_text"],
                    },
                )
                raise ReviewConflict("Suggestion conflicts with the current subtitle revision")

            connection.execute(
                """
                UPDATE review_subtitle_segments
                SET working_text = ?, revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (suggestion["suggested_text"], now, suggestion["segment_id"]),
            )
            connection.execute(
                """
                UPDATE review_suggestions
                SET status = 'approved', reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, suggestion_id),
            )
            self._audit(
                connection,
                actor=actor,
                action="suggestion_approved",
                entity_type="suggestion",
                entity_id=suggestion_id,
                payload={
                    "segment_id": suggestion["segment_id"],
                    "before": suggestion["working_text"],
                    "after": suggestion["suggested_text"],
                },
            )
            version = self._snapshot_video(
                connection,
                youtube_video_id=str(suggestion["youtube_video_id"]),
                actor=actor,
                source="suggestion_approval",
                source_ref=suggestion_id,
            )
            approved = connection.execute(
                "SELECT * FROM review_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
        return {"suggestion": dict(approved), "version": version}

    def reject_suggestion(
        self,
        *,
        suggestion_id: str,
        actor: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            suggestion = connection.execute(
                "SELECT * FROM review_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
            if suggestion is None:
                raise ReviewNotFound("Suggestion not found")
            if suggestion["status"] != "pending":
                raise ReviewConflict("Suggestion is no longer pending")
            connection.execute(
                """
                UPDATE review_suggestions
                SET status = 'rejected', reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, suggestion_id),
            )
            self._audit(
                connection,
                actor=actor,
                action="suggestion_rejected",
                entity_type="suggestion",
                entity_id=suggestion_id,
                payload={"reason": (reason or "").strip()},
            )
            row = connection.execute(
                "SELECT * FROM review_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
        return dict(row)

    def create_batch(
        self,
        *,
        find_text: str,
        replace_text: str,
        actor: str,
        youtube_video_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        needle = find_text.strip()
        if not needle:
            raise ValueError("find_text is required")
        batch_id = uuid.uuid4().hex
        now = _iso()
        with self.transaction() as connection:
            params: list[Any] = [needle]
            video_filter = ""
            if youtube_video_ids:
                unique_ids = list(dict.fromkeys(item.strip() for item in youtube_video_ids if item.strip()))
                if not unique_ids:
                    raise ValueError("youtube_video_ids contains no valid IDs")
                placeholders = ",".join("?" for _ in unique_ids)
                video_filter = f" AND youtube_video_id IN ({placeholders})"
                params.extend(unique_ids)
            rows = connection.execute(
                f"""
                SELECT id, youtube_video_id, revision, working_text
                FROM review_subtitle_segments
                WHERE instr(working_text, ?) > 0 {video_filter}
                ORDER BY youtube_video_id, segment_index
                """,
                tuple(params),
            ).fetchall()
            connection.execute(
                """
                INSERT INTO review_batch_replacements(
                    id, find_text, replace_text, status,
                    created_by_actor, created_at
                ) VALUES (?, ?, ?, 'draft', ?, ?)
                """,
                (batch_id, needle, replace_text, actor, now),
            )
            for row in rows:
                before = str(row["working_text"])
                after = before.replace(needle, replace_text)
                connection.execute(
                    """
                    INSERT INTO review_batch_replacement_items(
                        batch_id, segment_id, youtube_video_id, base_revision,
                        original_text_snapshot, proposed_text, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        batch_id,
                        row["id"],
                        row["youtube_video_id"],
                        row["revision"],
                        before,
                        after,
                    ),
                )
            self._audit(
                connection,
                actor=actor,
                action="batch_created",
                entity_type="batch_replacement",
                entity_id=batch_id,
                payload={
                    "find_text": needle,
                    "replace_text": replace_text,
                    "match_count": len(rows),
                    "youtube_video_ids": youtube_video_ids or [],
                },
            )
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            batch = connection.execute(
                "SELECT * FROM review_batch_replacements WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise ReviewNotFound("Batch replacement not found")
            rows = connection.execute(
                """
                SELECT i.*, seg.segment_index, seg.start_ms, seg.end_ms,
                       seg.working_text AS current_text, seg.revision AS current_revision,
                       v.title AS video_title
                FROM review_batch_replacement_items i
                JOIN review_subtitle_segments seg ON seg.id = i.segment_id
                JOIN review_videos v ON v.youtube_video_id = i.youtube_video_id
                WHERE i.batch_id = ?
                ORDER BY v.title, seg.segment_index
                """,
                (batch_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["conflict"] = (
                item["status"] == "pending"
                and (
                    int(item["base_revision"]) != int(item["current_revision"])
                    or str(item["original_text_snapshot"]) != str(item["current_text"])
                )
            )
            items.append(item)
        return {"batch": dict(batch), "items": items}

    def apply_batch(
        self,
        *,
        batch_id: str,
        actor: str,
        item_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        selected = set(int(item) for item in item_ids) if item_ids is not None else None
        now = _iso()
        versions: list[dict[str, Any]] = []
        applied_count = 0
        conflict_count = 0
        skipped_count = 0
        with self.transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM review_batch_replacements WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise ReviewNotFound("Batch replacement not found")
            if batch["status"] != "draft":
                raise ReviewConflict("Batch replacement is no longer a draft")
            rows = connection.execute(
                """
                SELECT i.*, seg.working_text, seg.revision
                FROM review_batch_replacement_items i
                JOIN review_subtitle_segments seg ON seg.id = i.segment_id
                WHERE i.batch_id = ? AND i.status = 'pending'
                ORDER BY i.youtube_video_id, i.id
                """,
                (batch_id,),
            ).fetchall()
            changed_by_video: dict[str, list[int]] = {}
            for row in rows:
                item_id = int(row["id"])
                if selected is not None and item_id not in selected:
                    connection.execute(
                        "UPDATE review_batch_replacement_items SET status = 'skipped' WHERE id = ?",
                        (item_id,),
                    )
                    skipped_count += 1
                    continue
                if (
                    int(row["base_revision"]) != int(row["revision"])
                    or str(row["original_text_snapshot"]) != str(row["working_text"])
                ):
                    connection.execute(
                        """
                        UPDATE review_batch_replacement_items
                        SET status = 'conflict', error = 'segment changed after batch preview'
                        WHERE id = ?
                        """,
                        (item_id,),
                    )
                    conflict_count += 1
                    continue
                connection.execute(
                    """
                    UPDATE review_subtitle_segments
                    SET working_text = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (row["proposed_text"], now, row["segment_id"]),
                )
                connection.execute(
                    "UPDATE review_batch_replacement_items SET status = 'applied' WHERE id = ?",
                    (item_id,),
                )
                changed_by_video.setdefault(str(row["youtube_video_id"]), []).append(item_id)
                applied_count += 1

            for video_id, applied_item_ids in changed_by_video.items():
                version = self._snapshot_video(
                    connection,
                    youtube_video_id=video_id,
                    actor=actor,
                    source="batch_replacement",
                    source_ref=batch_id,
                )
                versions.append(version)
                placeholders = ",".join("?" for _ in applied_item_ids)
                connection.execute(
                    f"""
                    UPDATE review_batch_replacement_items
                    SET applied_version_id = ?
                    WHERE id IN ({placeholders})
                    """,
                    (version["id"], *applied_item_ids),
                )

            connection.execute(
                """
                UPDATE review_batch_replacements
                SET status = 'applied', applied_at = ? WHERE id = ?
                """,
                (now, batch_id),
            )
            self._audit(
                connection,
                actor=actor,
                action="batch_applied",
                entity_type="batch_replacement",
                entity_id=batch_id,
                payload={
                    "applied_count": applied_count,
                    "conflict_count": conflict_count,
                    "skipped_count": skipped_count,
                    "version_ids": [item["id"] for item in versions],
                },
            )
        return {
            "batch": self.get_batch(batch_id),
            "versions": versions,
            "applied_count": applied_count,
            "conflict_count": conflict_count,
            "skipped_count": skipped_count,
        }

    def list_versions(
        self,
        *,
        youtube_video_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if youtube_video_id:
            where = "WHERE ver.youtube_video_id = ?"
            params.append(youtube_video_id)
        params.append(limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT ver.*, v.title AS video_title
                FROM review_subtitle_versions ver
                JOIN review_videos v ON v.youtube_video_id = ver.youtube_video_id
                {where}
                ORDER BY ver.created_at DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_version(self, version_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT ver.*, v.title AS video_title, v.caption_track_id AS current_caption_track_id
                FROM review_subtitle_versions ver
                JOIN review_videos v ON v.youtube_video_id = ver.youtube_video_id
                WHERE ver.id = ?
                """,
                (version_id,),
            ).fetchone()
        return self._row(row, "Subtitle version not found")

    def restore_version(self, *, version_id: str, actor: str) -> dict[str, Any]:
        with self.transaction() as connection:
            source = connection.execute(
                "SELECT * FROM review_subtitle_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if source is None:
                raise ReviewNotFound("Subtitle version not found")
            try:
                snapshot = json.loads(source["snapshot_json"])
            except json.JSONDecodeError as exc:
                raise ReviewConflict("Stored subtitle version snapshot is invalid") from exc
            current_rows = connection.execute(
                """
                SELECT id, segment_index, start_ms, end_ms, working_text
                FROM review_subtitle_segments
                WHERE youtube_video_id = ? ORDER BY segment_index
                """,
                (source["youtube_video_id"],),
            ).fetchall()
            current = [dict(row) for row in current_rows]
            if len(current) != len(snapshot):
                raise ReviewConflict("Stored version no longer matches the video's segment structure")
            for before, target in zip(current, snapshot, strict=True):
                if (
                    int(before["segment_index"]) != int(target["segment_index"])
                    or int(before["start_ms"]) != int(target["start_ms"])
                    or int(before["end_ms"]) != int(target["end_ms"])
                ):
                    raise ReviewConflict("Stored version timing/segment structure does not match")
            now = _iso()
            changed = 0
            for before, target in zip(current, snapshot, strict=True):
                target_text = str(target["working_text"])
                if str(before["working_text"]) == target_text:
                    continue
                connection.execute(
                    """
                    UPDATE review_subtitle_segments
                    SET working_text = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (target_text, now, before["id"]),
                )
                changed += 1
            restored = self._snapshot_video(
                connection,
                youtube_video_id=str(source["youtube_video_id"]),
                actor=actor,
                source="restore_version",
                source_ref=version_id,
            )
            self._audit(
                connection,
                actor=actor,
                action="version_restored",
                entity_type="subtitle_version",
                entity_id=version_id,
                payload={"new_version_id": restored["id"], "changed_segments": changed},
            )
        return {"restored_from": version_id, "version": restored, "changed_segments": changed}

    def mark_publish_success(
        self,
        *,
        version_id: str,
        caption_track_id: str,
        actor: str,
        youtube_response: dict[str, Any],
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            version = connection.execute(
                "SELECT * FROM review_subtitle_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if version is None:
                raise ReviewNotFound("Subtitle version not found")
            connection.execute(
                """
                UPDATE review_subtitle_versions
                SET publish_status = 'superseded'
                WHERE youtube_video_id = ? AND publish_status = 'published' AND id <> ?
                """,
                (version["youtube_video_id"], version_id),
            )
            connection.execute(
                """
                UPDATE review_subtitle_versions
                SET publish_status = 'published', published_at = ?,
                    youtube_caption_track_id = ?, youtube_response_json = ?,
                    publish_error = NULL
                WHERE id = ?
                """,
                (
                    now,
                    caption_track_id,
                    json.dumps(youtube_response, ensure_ascii=False, sort_keys=True),
                    version_id,
                ),
            )
            self._audit(
                connection,
                actor=actor,
                action="youtube_publish_succeeded",
                entity_type="subtitle_version",
                entity_id=version_id,
                payload={"caption_track_id": caption_track_id},
            )
            row = connection.execute(
                "SELECT * FROM review_subtitle_versions WHERE id = ?", (version_id,)
            ).fetchone()
        return dict(row)

    def mark_publish_failed(
        self,
        *,
        version_id: str,
        actor: str,
        error: str,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE review_subtitle_versions
                SET publish_status = 'publish_failed', publish_error = ?
                WHERE id = ?
                """,
                (error[:2000], version_id),
            )
            if cursor.rowcount != 1:
                raise ReviewNotFound("Subtitle version not found")
            self._audit(
                connection,
                actor=actor,
                action="youtube_publish_failed",
                entity_type="subtitle_version",
                entity_id=version_id,
                payload={"error": error[:1000]},
            )
            row = connection.execute(
                "SELECT * FROM review_subtitle_versions WHERE id = ?", (version_id,)
            ).fetchone()
        return dict(row)
