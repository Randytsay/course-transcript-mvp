"""Short-lived edit leases for collaborative subtitle review.

A video allows at most two active reviewers. Leases expire automatically when a
browser disappears, so an abandoned tab cannot permanently consume capacity.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .store import ReviewConflict, ReviewNotFound


class ReviewLeaseError(RuntimeError):
    pass


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ReviewLeaseStore:
    def __init__(self, database_path: Path, *, max_editors_per_video: int = 2):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if max_editors_per_video < 1 or max_editors_per_video > 10:
            raise ValueError("max_editors_per_video must be between 1 and 10")
        self.max_editors_per_video = max_editors_per_video
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
        )
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
                CREATE TABLE IF NOT EXISTS review_edit_leases (
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY(youtube_video_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS review_edit_lease_expiry_idx
                    ON review_edit_leases(youtube_video_id, expires_at);
                """
            )

    @staticmethod
    def _ttl_seconds(value: int) -> int:
        return max(60, min(int(value), 900))

    def _validate_subjects(
        self,
        connection: sqlite3.Connection,
        *,
        user_id: str,
        youtube_video_id: str,
    ) -> None:
        user = connection.execute(
            "SELECT status FROM review_users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise ReviewNotFound("Reviewer not found")
        if user["status"] != "active":
            raise ReviewConflict("Reviewer account is suspended")
        video = connection.execute(
            "SELECT 1 FROM review_videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if video is None:
            raise ReviewNotFound("Video not found")

    def acquire(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        ttl_seconds: int = 180,
    ) -> dict[str, Any]:
        ttl = self._ttl_seconds(ttl_seconds)
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl)
        token = secrets.token_urlsafe(36)
        token_hash = _digest(token)
        with self.transaction() as connection:
            self._validate_subjects(
                connection,
                user_id=user_id,
                youtube_video_id=youtube_video_id,
            )
            connection.execute(
                "DELETE FROM review_edit_leases WHERE expires_at <= ?",
                (_iso(now),),
            )
            existing = connection.execute(
                """
                SELECT 1 FROM review_edit_leases
                WHERE youtube_video_id = ? AND user_id = ?
                """,
                (youtube_video_id, user_id),
            ).fetchone()
            if existing is None:
                active = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count FROM review_edit_leases
                        WHERE youtube_video_id = ? AND expires_at > ?
                        """,
                        (youtube_video_id, _iso(now)),
                    ).fetchone()["count"]
                )
                if active >= self.max_editors_per_video:
                    raise ReviewConflict(
                        f"Video already has {self.max_editors_per_video} active reviewers"
                    )
                connection.execute(
                    """
                    INSERT INTO review_edit_leases(
                        youtube_video_id, user_id, token_hash, created_at,
                        last_heartbeat_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        youtube_video_id,
                        user_id,
                        token_hash,
                        _iso(now),
                        _iso(now),
                        _iso(expires),
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE review_edit_leases
                    SET token_hash = ?, last_heartbeat_at = ?, expires_at = ?
                    WHERE youtube_video_id = ? AND user_id = ?
                    """,
                    (
                        token_hash,
                        _iso(now),
                        _iso(expires),
                        youtube_video_id,
                        user_id,
                    ),
                )
        return {
            "youtube_video_id": youtube_video_id,
            "lease_token": token,
            "expires_at": _iso(expires),
            "heartbeat_after_seconds": max(30, ttl // 2),
            "max_editors": self.max_editors_per_video,
        }

    def heartbeat(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        lease_token: str,
        ttl_seconds: int = 180,
    ) -> dict[str, Any]:
        if not lease_token:
            raise ReviewLeaseError("Lease token is required")
        ttl = self._ttl_seconds(ttl_seconds)
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl)
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT expires_at FROM review_edit_leases
                WHERE youtube_video_id = ? AND user_id = ? AND token_hash = ?
                """,
                (youtube_video_id, user_id, _digest(lease_token)),
            ).fetchone()
            if row is None:
                raise ReviewLeaseError("Edit lease is invalid")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute(
                    """
                    DELETE FROM review_edit_leases
                    WHERE youtube_video_id = ? AND user_id = ?
                    """,
                    (youtube_video_id, user_id),
                )
                raise ReviewLeaseError("Edit lease has expired")
            connection.execute(
                """
                UPDATE review_edit_leases
                SET last_heartbeat_at = ?, expires_at = ?
                WHERE youtube_video_id = ? AND user_id = ?
                """,
                (_iso(now), _iso(expires), youtube_video_id, user_id),
            )
        return {
            "youtube_video_id": youtube_video_id,
            "expires_at": _iso(expires),
            "heartbeat_after_seconds": max(30, ttl // 2),
        }

    def release(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        lease_token: str,
    ) -> bool:
        if not lease_token:
            return False
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM review_edit_leases
                WHERE youtube_video_id = ? AND user_id = ? AND token_hash = ?
                """,
                (youtube_video_id, user_id, _digest(lease_token)),
            )
        return cursor.rowcount == 1

    def validate(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        lease_token: str,
    ) -> bool:
        if not lease_token:
            return False
        now = datetime.now(UTC)
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT expires_at FROM review_edit_leases
                WHERE youtube_video_id = ? AND user_id = ? AND token_hash = ?
                """,
                (youtube_video_id, user_id, _digest(lease_token)),
            ).fetchone()
        return bool(row and datetime.fromisoformat(row["expires_at"]) > now)

    def active_editors(self, youtube_video_id: str) -> list[dict[str, Any]]:
        now = _iso()
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM review_edit_leases WHERE expires_at <= ?",
                (now,),
            )
            rows = connection.execute(
                """
                SELECT l.user_id, u.display_name, u.avatar_url, l.expires_at
                FROM review_edit_leases l
                JOIN review_users u ON u.id = l.user_id
                WHERE l.youtube_video_id = ? AND l.expires_at > ?
                ORDER BY l.created_at
                """,
                (youtube_video_id, now),
            ).fetchall()
        return [dict(row) for row in rows]
