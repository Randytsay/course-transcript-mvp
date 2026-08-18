"""Owner approval gate for formal learning-content source versions."""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.review.admin_store import ReviewAdminStore
from app.review.store import ReviewConflict, ReviewNotFound


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class LearningSourceStore:
    """Pins one immutable subtitle version as the formal learning source per video."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.review_admin = ReviewAdminStore(self.database_path)
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
                CREATE TABLE IF NOT EXISTS learning_source_versions (
                    youtube_video_id TEXT PRIMARY KEY REFERENCES review_videos(youtube_video_id),
                    subtitle_version_id TEXT NOT NULL REFERENCES review_subtitle_versions(id),
                    source_sha256 TEXT NOT NULL,
                    approved_by_actor TEXT NOT NULL,
                    approved_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS learning_source_version_idx
                    ON learning_source_versions(subtitle_version_id);
                """
            )

    def approve_latest(self, *, youtube_video_id: str, actor: str) -> dict[str, Any]:
        latest = self.review_admin.list_versions(youtube_video_id=youtube_video_id, limit=1)
        if not latest:
            raise ReviewConflict(
                "No immutable subtitle version exists yet; finish subtitle review and create a version before approving learning content"
            )
        version = latest[0]
        now = _iso()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO learning_source_versions(
                    youtube_video_id, subtitle_version_id, source_sha256,
                    approved_by_actor, approved_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(youtube_video_id) DO UPDATE SET
                    subtitle_version_id = excluded.subtitle_version_id,
                    source_sha256 = excluded.source_sha256,
                    approved_by_actor = excluded.approved_by_actor,
                    approved_at = excluded.approved_at
                """,
                (
                    youtube_video_id,
                    version["id"],
                    version["content_sha256"],
                    actor,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM learning_source_versions WHERE youtube_video_id = ?",
                (youtube_video_id,),
            ).fetchone()
        return dict(row)

    def get(self, youtube_video_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT s.*, rv.version_number, rv.snapshot_json, rv.srt_text,
                       rv.content_sha256, rv.created_at AS version_created_at,
                       v.title AS video_title
                FROM learning_source_versions s
                JOIN review_subtitle_versions rv ON rv.id = s.subtitle_version_id
                JOIN review_videos v ON v.youtube_video_id = s.youtube_video_id
                WHERE s.youtube_video_id = ?
                """,
                (youtube_video_id,),
            ).fetchone()
        return dict(row) if row else None

    def require(self, youtube_video_id: str) -> dict[str, Any]:
        row = self.get(youtube_video_id)
        if row is None:
            raise ReviewNotFound("This lesson has not been approved as a formal learning source")
        return row

    def status(self, youtube_video_id: str) -> dict[str, Any]:
        source = self.get(youtube_video_id)
        latest = self.review_admin.list_versions(youtube_video_id=youtube_video_id, limit=1)
        latest_version = latest[0] if latest else None
        return {
            "source": source,
            "latest_version": latest_version,
            "source_is_latest": bool(
                source
                and latest_version
                and str(source["subtitle_version_id"]) == str(latest_version["id"])
            ),
        }
