"""SQLite-backed collaboration state for subtitle reviewers.

The review domain deliberately shares the existing SQLite database while using
only ``review_*`` tables. It does not mutate transcription jobs, provider
artifacts, or the existing subtitle-editor evidence.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator, Literal

AuthProvider = Literal["google", "line"]
ReviewRole = Literal["owner", "reviewer"]


class ReviewNotFound(LookupError):
    pass


class ReviewConflict(RuntimeError):
    pass


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def changed_char_count(before: str, after: str) -> int:
    """Return a human-oriented count of changed characters.

    Insertions/deletions count their affected characters. A replacement counts
    the larger side of the replacement span, so ``今`` -> ``經`` counts as one
    changed character rather than two edit operations.
    """
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            changed += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            changed += i2 - i1
        elif tag == "insert":
            changed += j2 - j1
    return changed


class ReviewStore:
    """Persistence boundary for reviewer identities, progress and suggestions."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS review_users (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    avatar_url TEXT,
                    role TEXT NOT NULL DEFAULT 'reviewer'
                        CHECK(role IN ('owner', 'reviewer')),
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'suspended')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_auth_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    provider TEXT NOT NULL CHECK(provider IN ('google', 'line')),
                    provider_subject TEXT NOT NULL,
                    email TEXT,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL,
                    UNIQUE(provider, provider_subject),
                    UNIQUE(user_id, provider)
                );

                CREATE TABLE IF NOT EXISTS review_videos (
                    youtube_video_id TEXT PRIMARY KEY,
                    playlist_id TEXT,
                    title TEXT NOT NULL,
                    duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
                    caption_track_id TEXT,
                    caption_language TEXT NOT NULL DEFAULT 'zh-TW',
                    caption_name TEXT,
                    imported_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_subtitle_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    youtube_video_id TEXT NOT NULL
                        REFERENCES review_videos(youtube_video_id),
                    segment_index INTEGER NOT NULL CHECK(segment_index > 0),
                    start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
                    end_ms INTEGER NOT NULL CHECK(end_ms > start_ms),
                    original_text TEXT NOT NULL,
                    working_text TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(youtube_video_id, segment_index)
                );

                CREATE TABLE IF NOT EXISTS review_suggestions (
                    id TEXT PRIMARY KEY,
                    segment_id INTEGER NOT NULL REFERENCES review_subtitle_segments(id),
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    base_segment_revision INTEGER NOT NULL CHECK(base_segment_revision >= 0),
                    original_text_snapshot TEXT NOT NULL,
                    suggested_text TEXT NOT NULL,
                    changed_chars INTEGER NOT NULL CHECK(changed_chars > 0),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'approved', 'rejected')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_by TEXT REFERENCES review_users(id),
                    reviewed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS review_suggestion_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suggestion_id TEXT NOT NULL REFERENCES review_suggestions(id),
                    event_type TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL REFERENCES review_users(id),
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_video_progress (
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    youtube_video_id TEXT NOT NULL
                        REFERENCES review_videos(youtube_video_id),
                    last_playback_ms INTEGER NOT NULL DEFAULT 0
                        CHECK(last_playback_ms >= 0),
                    reviewed_until_ms INTEGER NOT NULL DEFAULT 0
                        CHECK(reviewed_until_ms >= 0),
                    last_segment_index INTEGER,
                    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0, 1)),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, youtube_video_id)
                );

                CREATE INDEX IF NOT EXISTS review_identity_user_idx
                    ON review_auth_identities(user_id);
                CREATE INDEX IF NOT EXISTS review_segments_video_time_idx
                    ON review_subtitle_segments(youtube_video_id, start_ms, segment_index);
                CREATE INDEX IF NOT EXISTS review_suggestions_user_idx
                    ON review_suggestions(user_id, created_at);
                CREATE INDEX IF NOT EXISTS review_suggestions_segment_idx
                    ON review_suggestions(segment_id, created_at);
                CREATE INDEX IF NOT EXISTS review_suggestions_status_idx
                    ON review_suggestions(status, created_at);
                CREATE INDEX IF NOT EXISTS review_progress_video_idx
                    ON review_video_progress(youtube_video_id, updated_at);
                """
            )

    @staticmethod
    def _provider(provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in {"google", "line"}:
            raise ValueError("provider must be google or line")
        return normalized

    @staticmethod
    def _row(row: sqlite3.Row | None, message: str) -> dict[str, Any]:
        if row is None:
            raise ReviewNotFound(message)
        return dict(row)

    def get_user(self, user_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM review_users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row(row, "Reviewer not found")

    def get_or_create_user_for_identity(
        self,
        *,
        provider: AuthProvider | str,
        provider_subject: str,
        display_name: str,
        email: str | None = None,
        avatar_url: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an OAuth/OIDC identity; first login is active by default."""
        provider = self._provider(provider)
        subject = provider_subject.strip()
        name = display_name.strip()
        if not subject or not name:
            raise ValueError("provider_subject and display_name are required")
        now = _iso()
        with self.transaction() as connection:
            identity = connection.execute(
                """
                SELECT i.user_id, u.status
                FROM review_auth_identities i
                JOIN review_users u ON u.id = i.user_id
                WHERE i.provider = ? AND i.provider_subject = ?
                """,
                (provider, subject),
            ).fetchone()
            if identity is not None:
                if identity["status"] != "active":
                    raise ReviewConflict("Reviewer account is suspended")
                connection.execute(
                    """
                    UPDATE review_users
                    SET display_name = ?, avatar_url = COALESCE(?, avatar_url),
                        updated_at = ?, last_login_at = ?
                    WHERE id = ?
                    """,
                    (name, avatar_url, now, now, identity["user_id"]),
                )
                connection.execute(
                    """
                    UPDATE review_auth_identities
                    SET email = COALESCE(?, email), last_login_at = ?
                    WHERE provider = ? AND provider_subject = ?
                    """,
                    (email, now, provider, subject),
                )
                user_id = str(identity["user_id"])
            else:
                user_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO review_users(
                        id, display_name, avatar_url, role, status,
                        created_at, updated_at, last_login_at
                    ) VALUES (?, ?, ?, 'reviewer', 'active', ?, ?, ?)
                    """,
                    (user_id, name, avatar_url, now, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO review_auth_identities(
                        user_id, provider, provider_subject, email,
                        created_at, last_login_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, provider, subject, email, now, now),
                )
            row = connection.execute(
                "SELECT * FROM review_users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row(row, "Reviewer not found")

    def link_identity(
        self,
        *,
        user_id: str,
        provider: AuthProvider | str,
        provider_subject: str,
        email: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly bind a second login provider to the same logical user."""
        provider = self._provider(provider)
        subject = provider_subject.strip()
        if not subject:
            raise ValueError("provider_subject is required")
        now = _iso()
        with self.transaction() as connection:
            user = connection.execute(
                "SELECT id, status FROM review_users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                raise ReviewNotFound("Reviewer not found")
            if user["status"] != "active":
                raise ReviewConflict("Reviewer account is suspended")
            existing = connection.execute(
                """
                SELECT user_id FROM review_auth_identities
                WHERE provider = ? AND provider_subject = ?
                """,
                (provider, subject),
            ).fetchone()
            if existing is not None and existing["user_id"] != user_id:
                raise ReviewConflict("Login identity is already linked to another reviewer")
            same_provider = connection.execute(
                """
                SELECT provider_subject FROM review_auth_identities
                WHERE user_id = ? AND provider = ?
                """,
                (user_id, provider),
            ).fetchone()
            if same_provider is not None and same_provider["provider_subject"] != subject:
                raise ReviewConflict("Reviewer already has a different identity for this provider")
            connection.execute(
                """
                INSERT INTO review_auth_identities(
                    user_id, provider, provider_subject, email,
                    created_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_subject) DO UPDATE SET
                    email = COALESCE(excluded.email, review_auth_identities.email),
                    last_login_at = excluded.last_login_at
                """,
                (user_id, provider, subject, email, now, now),
            )
            rows = connection.execute(
                """
                SELECT provider, provider_subject, email, created_at, last_login_at
                FROM review_auth_identities
                WHERE user_id = ? ORDER BY provider
                """,
                (user_id,),
            ).fetchall()
        return {"user_id": user_id, "identities": [dict(row) for row in rows]}

    def set_user_role(self, *, user_id: str, role: ReviewRole | str) -> dict[str, Any]:
        if role not in {"owner", "reviewer"}:
            raise ValueError("role must be owner or reviewer")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE review_users SET role = ?, updated_at = ? WHERE id = ?",
                (role, _iso(), user_id),
            )
            if cursor.rowcount != 1:
                raise ReviewNotFound("Reviewer not found")
            row = connection.execute(
                "SELECT * FROM review_users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row(row, "Reviewer not found")

    def upsert_video(
        self,
        *,
        youtube_video_id: str,
        title: str,
        playlist_id: str | None = None,
        duration_ms: int | None = None,
        caption_track_id: str | None = None,
        caption_language: str = "zh-TW",
        caption_name: str | None = None,
    ) -> dict[str, Any]:
        video_id = youtube_video_id.strip()
        title = title.strip()
        if not video_id or not title:
            raise ValueError("youtube_video_id and title are required")
        if duration_ms is not None and duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        now = _iso()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO review_videos(
                    youtube_video_id, playlist_id, title, duration_ms,
                    caption_track_id, caption_language, caption_name,
                    imported_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(youtube_video_id) DO UPDATE SET
                    playlist_id = excluded.playlist_id,
                    title = excluded.title,
                    duration_ms = excluded.duration_ms,
                    caption_track_id = excluded.caption_track_id,
                    caption_language = excluded.caption_language,
                    caption_name = excluded.caption_name,
                    updated_at = excluded.updated_at
                """,
                (
                    video_id, playlist_id, title, duration_ms, caption_track_id,
                    caption_language, caption_name, now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM review_videos WHERE youtube_video_id = ?", (video_id,)
            ).fetchone()
        return self._row(row, "Video not found")

    @staticmethod
    def _normalized_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for position, item in enumerate(segments, 1):
            index = int(item.get("segment_index", item.get("index", position)))
            start_ms = int(item["start_ms"])
            end_ms = int(item["end_ms"])
            text = str(item.get("text", item.get("original_text", ""))).strip()
            if index <= 0 or start_ms < 0 or end_ms <= start_ms or not text:
                raise ValueError(f"invalid subtitle segment at position {position}")
            normalized.append(
                {
                    "segment_index": index,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                }
            )
        if not normalized:
            raise ValueError("at least one subtitle segment is required")
        if len({item["segment_index"] for item in normalized}) != len(normalized):
            raise ValueError("segment_index values must be unique")
        normalized.sort(key=lambda item: item["segment_index"])
        return normalized

    def import_subtitle_segments(
        self,
        *,
        youtube_video_id: str,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Perform the initial caption import without overwriting review history."""
        normalized = self._normalized_segments(segments)
        now = _iso()
        with self.transaction() as connection:
            video = connection.execute(
                "SELECT 1 FROM review_videos WHERE youtube_video_id = ?",
                (youtube_video_id,),
            ).fetchone()
            if video is None:
                raise ReviewNotFound("Video not found")
            existing = connection.execute(
                """
                SELECT COUNT(*) AS count FROM review_subtitle_segments
                WHERE youtube_video_id = ?
                """,
                (youtube_video_id,),
            ).fetchone()["count"]
            if existing:
                raise ReviewConflict(
                    "Subtitle segments already exist; use a later versioned refresh flow"
                )
            connection.executemany(
                """
                INSERT INTO review_subtitle_segments(
                    youtube_video_id, segment_index, start_ms, end_ms,
                    original_text, working_text, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                [
                    (
                        youtube_video_id,
                        item["segment_index"],
                        item["start_ms"],
                        item["end_ms"],
                        item["text"],
                        item["text"],
                        now,
                        now,
                    )
                    for item in normalized
                ],
            )
            rows = connection.execute(
                """
                SELECT * FROM review_subtitle_segments
                WHERE youtube_video_id = ? ORDER BY segment_index
                """,
                (youtube_video_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_segments(self, youtube_video_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM review_subtitle_segments
                WHERE youtube_video_id = ? ORDER BY segment_index
                """,
                (youtube_video_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def submit_suggestion(
        self,
        *,
        segment_id: int,
        user_id: str,
        suggested_text: str,
    ) -> dict[str, Any]:
        """Append a suggestion; contribution credit starts immediately on submit."""
        text = suggested_text.strip()
        if not text:
            raise ValueError("suggested_text is required")
        now = _iso()
        suggestion_id = uuid.uuid4().hex
        with self.transaction() as connection:
            user = connection.execute(
                "SELECT status FROM review_users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                raise ReviewNotFound("Reviewer not found")
            if user["status"] != "active":
                raise ReviewConflict("Reviewer account is suspended")
            segment = connection.execute(
                "SELECT * FROM review_subtitle_segments WHERE id = ?", (segment_id,)
            ).fetchone()
            if segment is None:
                raise ReviewNotFound("Subtitle segment not found")
            before = str(segment["working_text"])
            if text == before:
                raise ReviewConflict("Suggestion must change the subtitle text")
            changed = changed_char_count(before, text)
            connection.execute(
                """
                INSERT INTO review_suggestions(
                    id, segment_id, user_id, base_segment_revision,
                    original_text_snapshot, suggested_text, changed_chars,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    suggestion_id, segment_id, user_id, int(segment["revision"]),
                    before, text, changed, now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO review_suggestion_events(
                    suggestion_id, event_type, actor_user_id, payload_json, created_at
                ) VALUES (?, 'submitted', ?, ?, ?)
                """,
                (
                    suggestion_id,
                    user_id,
                    json.dumps(
                        {"before": before, "after": text, "changed_chars": changed},
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM review_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
        return self._row(row, "Suggestion not found")

    def revise_suggestion(
        self,
        *,
        suggestion_id: str,
        user_id: str,
        suggested_text: str,
    ) -> dict[str, Any]:
        """Revise the same pending suggestion without inflating contribution count."""
        text = suggested_text.strip()
        if not text:
            raise ValueError("suggested_text is required")
        now = _iso()
        with self.transaction() as connection:
            suggestion = connection.execute(
                "SELECT * FROM review_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
            if suggestion is None:
                raise ReviewNotFound("Suggestion not found")
            if suggestion["user_id"] != user_id:
                raise ReviewConflict("Only the original reviewer can revise this suggestion")
            if suggestion["status"] != "pending":
                raise ReviewConflict("Only pending suggestions can be revised")
            before_snapshot = str(suggestion["original_text_snapshot"])
            if text == before_snapshot:
                raise ReviewConflict("Suggestion must change the subtitle text")
            changed = changed_char_count(before_snapshot, text)
            connection.execute(
                """
                UPDATE review_suggestions
                SET suggested_text = ?, changed_chars = ?, updated_at = ?
                WHERE id = ?
                """,
                (text, changed, now, suggestion_id),
            )
            connection.execute(
                """
                INSERT INTO review_suggestion_events(
                    suggestion_id, event_type, actor_user_id, payload_json, created_at
                ) VALUES (?, 'revised', ?, ?, ?)
                """,
                (
                    suggestion_id,
                    user_id,
                    json.dumps(
                        {
                            "previous_suggestion": suggestion["suggested_text"],
                            "after": text,
                            "changed_chars": changed,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM review_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
        return self._row(row, "Suggestion not found")

    def submit_batch_replace_suggestions(
        self,
        *,
        youtube_video_id: str,
        user_id: str,
        find_text: str,
        replace_text: str,
    ) -> dict[str, Any]:
        """Create or revise pending suggestions for an exact text replacement.

        The replacement is scoped to one video and remains in the normal
        pending-suggestion workflow. A reviewer's existing pending suggestion
        is treated as the visible source text and revised in place; formal
        subtitle text is never changed here.
        """
        needle = find_text
        replacement = replace_text
        if not needle.strip():
            raise ValueError("find_text is required")
        if not replacement.strip():
            raise ValueError("replace_text is required")
        batch_id = uuid.uuid4().hex
        now = _iso()
        suggestions: list[dict[str, Any]] = []
        with self.transaction() as connection:
            user = connection.execute(
                "SELECT status FROM review_users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                raise ReviewNotFound("Reviewer not found")
            if user["status"] != "active":
                raise ReviewConflict("Reviewer account is suspended")
            video = connection.execute(
                "SELECT youtube_video_id FROM review_videos WHERE youtube_video_id = ?",
                (youtube_video_id,),
            ).fetchone()
            if video is None:
                raise ReviewNotFound("Video not found")
            rows = connection.execute(
                """
                SELECT
                    seg.*,
                    pending.id AS pending_id,
                    pending.original_text_snapshot AS pending_original_text,
                    pending.suggested_text AS pending_suggested_text
                FROM review_subtitle_segments seg
                LEFT JOIN review_suggestions pending
                    ON pending.id = (
                        SELECT s.id
                        FROM review_suggestions s
                        WHERE s.segment_id = seg.id
                          AND s.user_id = ?
                          AND s.status = 'pending'
                        ORDER BY s.updated_at DESC, s.created_at DESC
                        LIMIT 1
                    )
                WHERE seg.youtube_video_id = ?
                ORDER BY seg.segment_index
                """,
                (user_id, youtube_video_id),
            ).fetchall()
            for row in rows:
                before = str(row["pending_suggested_text"] or row["working_text"])
                if needle not in before:
                    continue
                after = before.replace(needle, replacement)
                if after == before:
                    continue
                if not after.strip():
                    raise ValueError("批次取代後不能留下空白字幕")
                if len(after) > 4000:
                    raise ValueError("批次取代後的字幕超過單段 4000 字限制")
                pending_id = row["pending_id"]
                if pending_id:
                    original_snapshot = str(row["pending_original_text"])
                    if after == original_snapshot:
                        continue
                    changed = changed_char_count(original_snapshot, after)
                    connection.execute(
                        """
                        UPDATE review_suggestions
                        SET suggested_text = ?, changed_chars = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (after, changed, now, str(pending_id)),
                    )
                    connection.execute(
                        """
                        INSERT INTO review_suggestion_events(
                            suggestion_id, event_type, actor_user_id, payload_json, created_at
                        ) VALUES (?, 'revised', ?, ?, ?)
                        """,
                        (
                            str(pending_id),
                            user_id,
                            json.dumps(
                                {
                                    "previous_suggestion": row["pending_suggested_text"],
                                    "after": after,
                                    "changed_chars": changed,
                                    "source": "batch_replace",
                                    "batch_id": batch_id,
                                    "find_text": needle,
                                    "replace_text": replacement,
                                },
                                ensure_ascii=False,
                            ),
                            now,
                        ),
                    )
                    suggestion_id = str(pending_id)
                    created = False
                else:
                    suggestion_id = uuid.uuid4().hex
                    original_snapshot = str(row["working_text"])
                    changed = changed_char_count(original_snapshot, after)
                    if changed <= 0:
                        continue
                    connection.execute(
                        """
                        INSERT INTO review_suggestions(
                            id, segment_id, user_id, base_segment_revision,
                            original_text_snapshot, suggested_text, changed_chars,
                            status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (
                            suggestion_id,
                            int(row["id"]),
                            user_id,
                            int(row["revision"]),
                            original_snapshot,
                            after,
                            changed,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO review_suggestion_events(
                            suggestion_id, event_type, actor_user_id, payload_json, created_at
                        ) VALUES (?, 'submitted', ?, ?, ?)
                        """,
                        (
                            suggestion_id,
                            user_id,
                            json.dumps(
                                {
                                    "before": original_snapshot,
                                    "after": after,
                                    "changed_chars": changed,
                                    "source": "batch_replace",
                                    "batch_id": batch_id,
                                    "find_text": needle,
                                    "replace_text": replacement,
                                },
                                ensure_ascii=False,
                            ),
                            now,
                        ),
                    )
                    created = True
                suggestions.append(
                    {
                        "id": suggestion_id,
                        "segment_id": int(row["id"]),
                        "segment_index": int(row["segment_index"]),
                        "suggested_text": after,
                        "created": created,
                    }
                )
        if not suggestions:
            raise ReviewConflict("找不到可套用的字幕文字")
        return {
            "batch_id": batch_id,
            "matched_count": len(suggestions),
            "created_count": sum(1 for item in suggestions if item["created"]),
            "revised_count": sum(1 for item in suggestions if not item["created"]),
            "suggestions": suggestions,
        }

    def update_progress(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        last_playback_ms: int,
        reviewed_until_ms: int | None = None,
        last_segment_index: int | None = None,
        completed: bool = False,
    ) -> dict[str, Any]:
        if last_playback_ms < 0 or (reviewed_until_ms is not None and reviewed_until_ms < 0):
            raise ValueError("progress values must be non-negative")
        reviewed = reviewed_until_ms if reviewed_until_ms is not None else 0
        now = _iso()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO review_video_progress(
                    user_id, youtube_video_id, last_playback_ms,
                    reviewed_until_ms, last_segment_index, completed, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, youtube_video_id) DO UPDATE SET
                    last_playback_ms = excluded.last_playback_ms,
                    reviewed_until_ms = MAX(
                        review_video_progress.reviewed_until_ms,
                        excluded.reviewed_until_ms
                    ),
                    last_segment_index = COALESCE(
                        excluded.last_segment_index,
                        review_video_progress.last_segment_index
                    ),
                    completed = MAX(review_video_progress.completed, excluded.completed),
                    updated_at = excluded.updated_at
                """,
                (
                    user_id, youtube_video_id, last_playback_ms, reviewed,
                    last_segment_index, 1 if completed else 0, now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM review_video_progress
                WHERE user_id = ? AND youtube_video_id = ?
                """,
                (user_id, youtube_video_id),
            ).fetchone()
        return self._row(row, "Progress not found")

    def set_completion(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        completed: bool,
    ) -> dict[str, Any]:
        """Explicitly set review completion without changing playback progress.

        Ordinary playback/progress writes remain monotonic so that background
        watch updates cannot accidentally reopen a completed review. This
        method is reserved for the reviewer's explicit status control.
        """
        now = _iso()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO review_video_progress(
                    user_id, youtube_video_id, completed, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, youtube_video_id) DO UPDATE SET
                    completed = excluded.completed,
                    updated_at = excluded.updated_at
                """,
                (user_id, youtube_video_id, 1 if completed else 0, now),
            )
            row = connection.execute(
                """
                SELECT * FROM review_video_progress
                WHERE user_id = ? AND youtube_video_id = ?
                """,
                (user_id, youtube_video_id),
            ).fetchone()
        return self._row(row, "Progress not found")

    def get_resume_point(self, user_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT p.*, v.title
                FROM review_video_progress p
                JOIN review_videos v ON v.youtube_video_id = p.youtube_video_id
                WHERE p.user_id = ?
                ORDER BY p.updated_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def contribution_leaderboard(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                WITH suggestion_totals AS (
                    SELECT
                        s.user_id,
                        COUNT(*) AS suggestions_sent,
                        COALESCE(SUM(s.changed_chars), 0) AS changed_chars,
                        COUNT(DISTINCT seg.youtube_video_id) AS videos_contributed
                    FROM review_suggestions s
                    JOIN review_subtitle_segments seg ON seg.id = s.segment_id
                    GROUP BY s.user_id
                ),
                completion_totals AS (
                    SELECT
                        user_id,
                        SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS completed_videos
                    FROM review_video_progress
                    GROUP BY user_id
                )
                SELECT
                    u.id AS user_id,
                    u.display_name,
                    u.avatar_url,
                    COALESCE(s.suggestions_sent, 0) AS suggestions_sent,
                    COALESCE(s.changed_chars, 0) AS changed_chars,
                    COALESCE(s.videos_contributed, 0) AS videos_contributed,
                    COALESCE(c.completed_videos, 0) AS completed_videos
                FROM review_users u
                LEFT JOIN suggestion_totals s ON s.user_id = u.id
                LEFT JOIN completion_totals c ON c.user_id = u.id
                WHERE u.status = 'active'
                ORDER BY suggestions_sent DESC, changed_chars DESC,
                         videos_contributed DESC, u.created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def user_contribution_detail(self, user_id: str) -> dict[str, Any]:
        user = self.get_user(user_id)
        with closing(self.connect()) as connection:
            totals = connection.execute(
                """
                SELECT
                    COUNT(s.id) AS suggestions_sent,
                    COALESCE(SUM(s.changed_chars), 0) AS changed_chars,
                    COUNT(DISTINCT seg.youtube_video_id) AS videos_contributed
                FROM review_suggestions s
                JOIN review_subtitle_segments seg ON seg.id = s.segment_id
                WHERE s.user_id = ?
                """,
                (user_id,),
            ).fetchone()
            completed = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM review_video_progress
                WHERE user_id = ? AND completed = 1
                """,
                (user_id,),
            ).fetchone()["count"]
            videos = connection.execute(
                """
                SELECT
                    v.youtube_video_id,
                    v.title,
                    COUNT(s.id) AS suggestions_sent,
                    COALESCE(SUM(s.changed_chars), 0) AS changed_chars,
                    MAX(COALESCE(p.completed, 0)) AS completed
                FROM review_suggestions s
                JOIN review_subtitle_segments seg ON seg.id = s.segment_id
                JOIN review_videos v ON v.youtube_video_id = seg.youtube_video_id
                LEFT JOIN review_video_progress p
                    ON p.user_id = s.user_id
                   AND p.youtube_video_id = seg.youtube_video_id
                WHERE s.user_id = ?
                GROUP BY v.youtube_video_id, v.title
                ORDER BY MAX(s.updated_at) DESC
                """,
                (user_id,),
            ).fetchall()
        return {
            "user": user,
            "suggestions_sent": int(totals["suggestions_sent"]),
            "changed_chars": int(totals["changed_chars"]),
            "videos_contributed": int(totals["videos_contributed"]),
            "completed_videos": int(completed),
            "videos": [dict(row) for row in videos],
        }
