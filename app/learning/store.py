"""SQLite persistence for learning progress, notes, review and AI artifacts.

The learning domain deliberately shares the reviewer identity/video/version data
while keeping all new state under ``learning_*`` tables. Learning actions never
publish YouTube captions or mutate subtitle timing.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

from app.review.admin_store import ReviewAdminStore
from app.review.store import ReviewConflict, ReviewNotFound

LearningStatus = Literal["not_started", "in_progress", "completed"]
FlashcardRating = Literal["again", "hard", "good", "easy"]


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class LearningStore:
    """Persistence boundary for the one-stop learning experience."""

    REVIEW_INTERVALS_DAYS = (1, 3, 7, 14, 30)
    FLASHCARD_INTERVALS = {"again": 1, "hard": 3, "good": 7, "easy": 14}

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure reviewer and immutable-version tables exist before our FKs.
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
                CREATE TABLE IF NOT EXISTS learning_video_state (
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    learning_status TEXT NOT NULL DEFAULT 'not_started'
                        CHECK(learning_status IN ('not_started', 'in_progress', 'completed')),
                    saved INTEGER NOT NULL DEFAULT 0 CHECK(saved IN (0, 1)),
                    started_at TEXT,
                    completed_at TEXT,
                    last_interaction_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, youtube_video_id)
                );

                CREATE TABLE IF NOT EXISTS learning_bookmarks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    segment_id INTEGER REFERENCES review_subtitle_segments(id),
                    start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
                    label TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learning_notes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    segment_id INTEGER REFERENCES review_subtitle_segments(id),
                    start_ms INTEGER CHECK(start_ms IS NULL OR start_ms >= 0),
                    title TEXT,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learning_artifacts (
                    id TEXT PRIMARY KEY,
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    subtitle_version_id TEXT NOT NULL REFERENCES review_subtitle_versions(id),
                    source_sha256 TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'stale')),
                    created_by_actor TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    UNIQUE(youtube_video_id, subtitle_version_id, artifact_type, prompt_version)
                );

                CREATE TABLE IF NOT EXISTS learning_generation_jobs (
                    id TEXT PRIMARY KEY,
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    subtitle_version_id TEXT NOT NULL REFERENCES review_subtitle_versions(id),
                    artifact_type TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK(status IN ('running', 'completed', 'failed')),
                    actor TEXT NOT NULL,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS learning_review_schedule (
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    stage INTEGER NOT NULL DEFAULT 0 CHECK(stage >= 0),
                    next_due_at TEXT,
                    last_reviewed_at TEXT,
                    completed_cycles INTEGER NOT NULL DEFAULT 0 CHECK(completed_cycles >= 0),
                    PRIMARY KEY(user_id, youtube_video_id)
                );

                CREATE TABLE IF NOT EXISTS learning_quiz_attempts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    youtube_video_id TEXT NOT NULL REFERENCES review_videos(youtube_video_id),
                    artifact_id TEXT REFERENCES learning_artifacts(id),
                    score INTEGER NOT NULL CHECK(score >= 0),
                    total INTEGER NOT NULL CHECK(total > 0),
                    answers_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learning_flashcard_progress (
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    artifact_id TEXT NOT NULL REFERENCES learning_artifacts(id),
                    card_key TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK(rating IN ('again', 'hard', 'good', 'easy')),
                    interval_days INTEGER NOT NULL CHECK(interval_days > 0),
                    next_due_at TEXT NOT NULL,
                    last_reviewed_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, artifact_id, card_key)
                );

                CREATE INDEX IF NOT EXISTS learning_state_status_idx
                    ON learning_video_state(user_id, learning_status, last_interaction_at DESC);
                CREATE INDEX IF NOT EXISTS learning_bookmarks_user_idx
                    ON learning_bookmarks(user_id, youtube_video_id, start_ms);
                CREATE INDEX IF NOT EXISTS learning_notes_user_idx
                    ON learning_notes(user_id, youtube_video_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS learning_artifacts_video_idx
                    ON learning_artifacts(youtube_video_id, generated_at DESC);
                CREATE INDEX IF NOT EXISTS learning_review_due_idx
                    ON learning_review_schedule(user_id, next_due_at);
                CREATE INDEX IF NOT EXISTS learning_quiz_user_idx
                    ON learning_quiz_attempts(user_id, youtube_video_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS learning_flashcard_due_idx
                    ON learning_flashcard_progress(user_id, next_due_at);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None, message: str) -> dict[str, Any]:
        if row is None:
            raise ReviewNotFound(message)
        return dict(row)

    def _video_exists(self, connection: sqlite3.Connection, youtube_video_id: str) -> None:
        row = connection.execute(
            "SELECT 1 FROM review_videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if row is None:
            raise ReviewNotFound("Learning video not found")

    @staticmethod
    def _safe_json(raw: str | None, fallback: Any) -> Any:
        try:
            return json.loads(raw or "")
        except (TypeError, json.JSONDecodeError):
            return fallback

    def upsert_learning_state(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        learning_status: LearningStatus | str | None = None,
        saved: bool | None = None,
    ) -> dict[str, Any]:
        status = learning_status.strip() if isinstance(learning_status, str) else None
        if status is not None and status not in {"not_started", "in_progress", "completed"}:
            raise ValueError("learning_status must be not_started, in_progress or completed")
        now = _iso()
        with self.transaction() as connection:
            self._video_exists(connection, youtube_video_id)
            existing = connection.execute(
                "SELECT * FROM learning_video_state WHERE user_id = ? AND youtube_video_id = ?",
                (user_id, youtube_video_id),
            ).fetchone()
            current_status = str(existing["learning_status"]) if existing else "not_started"
            next_status = status or current_status
            current_saved = bool(existing["saved"]) if existing else False
            next_saved = current_saved if saved is None else bool(saved)
            started_at = existing["started_at"] if existing else None
            completed_at = existing["completed_at"] if existing else None
            if next_status in {"in_progress", "completed"} and not started_at:
                started_at = now
            if next_status == "completed" and not completed_at:
                completed_at = now
            if next_status != "completed":
                completed_at = None
            connection.execute(
                """
                INSERT INTO learning_video_state(
                    user_id, youtube_video_id, learning_status, saved,
                    started_at, completed_at, last_interaction_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, youtube_video_id) DO UPDATE SET
                    learning_status = excluded.learning_status,
                    saved = excluded.saved,
                    started_at = excluded.started_at,
                    completed_at = excluded.completed_at,
                    last_interaction_at = excluded.last_interaction_at
                """,
                (
                    user_id,
                    youtube_video_id,
                    next_status,
                    1 if next_saved else 0,
                    started_at,
                    completed_at,
                    now,
                ),
            )
            if next_status == "completed":
                due = _iso(datetime.now(UTC) + timedelta(days=self.REVIEW_INTERVALS_DAYS[0]))
                connection.execute(
                    """
                    INSERT INTO learning_review_schedule(
                        user_id, youtube_video_id, stage, next_due_at,
                        last_reviewed_at, completed_cycles
                    ) VALUES (?, ?, 0, ?, NULL, 0)
                    ON CONFLICT(user_id, youtube_video_id) DO UPDATE SET
                        next_due_at = COALESCE(learning_review_schedule.next_due_at, excluded.next_due_at)
                    """,
                    (user_id, youtube_video_id, due),
                )
            row = connection.execute(
                "SELECT * FROM learning_video_state WHERE user_id = ? AND youtube_video_id = ?",
                (user_id, youtube_video_id),
            ).fetchone()
        return self._row(row, "Learning state not found")

    def create_bookmark(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        start_ms: int,
        segment_id: int | None = None,
        label: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        bookmark_id = uuid.uuid4().hex
        now = _iso()
        clean_label = (label or "").strip()[:200] or None
        clean_note = (note or "").strip()[:2000] or None
        with self.transaction() as connection:
            self._video_exists(connection, youtube_video_id)
            if segment_id is not None:
                segment = connection.execute(
                    "SELECT 1 FROM review_subtitle_segments WHERE id = ? AND youtube_video_id = ?",
                    (segment_id, youtube_video_id),
                ).fetchone()
                if segment is None:
                    raise ReviewNotFound("Subtitle segment not found")
            connection.execute(
                """
                INSERT INTO learning_bookmarks(
                    id, user_id, youtube_video_id, segment_id, start_ms,
                    label, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bookmark_id,
                    user_id,
                    youtube_video_id,
                    segment_id,
                    start_ms,
                    clean_label,
                    clean_note,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM learning_bookmarks WHERE id = ?", (bookmark_id,)
            ).fetchone()
        return self._row(row, "Bookmark not found")

    def list_bookmarks(self, *, user_id: str, youtube_video_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        where = "b.user_id = ?"
        if youtube_video_id:
            where += " AND b.youtube_video_id = ?"
            params.append(youtube_video_id)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT b.*, v.title AS video_title
                FROM learning_bookmarks b
                JOIN review_videos v ON v.youtube_video_id = b.youtube_video_id
                WHERE {where}
                ORDER BY b.updated_at DESC
                LIMIT 1000
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_bookmark(self, *, user_id: str, bookmark_id: str) -> bool:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT user_id FROM learning_bookmarks WHERE id = ?", (bookmark_id,)
            ).fetchone()
            if row is None:
                raise ReviewNotFound("Bookmark not found")
            if str(row["user_id"]) != user_id:
                raise ReviewConflict("Bookmark belongs to another learner")
            connection.execute("DELETE FROM learning_bookmarks WHERE id = ?", (bookmark_id,))
        return True

    def create_note(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        body: str,
        title: str | None = None,
        start_ms: int | None = None,
        segment_id: int | None = None,
    ) -> dict[str, Any]:
        text = body.strip()
        if not text:
            raise ValueError("Note body is required")
        if len(text) > 20000:
            raise ValueError("Note body is too long")
        if start_ms is not None and start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        note_id = uuid.uuid4().hex
        now = _iso()
        clean_title = (title or "").strip()[:200] or None
        with self.transaction() as connection:
            self._video_exists(connection, youtube_video_id)
            if segment_id is not None:
                segment = connection.execute(
                    "SELECT 1 FROM review_subtitle_segments WHERE id = ? AND youtube_video_id = ?",
                    (segment_id, youtube_video_id),
                ).fetchone()
                if segment is None:
                    raise ReviewNotFound("Subtitle segment not found")
            connection.execute(
                """
                INSERT INTO learning_notes(
                    id, user_id, youtube_video_id, segment_id, start_ms,
                    title, body, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    user_id,
                    youtube_video_id,
                    segment_id,
                    start_ms,
                    clean_title,
                    text,
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM learning_notes WHERE id = ?", (note_id,)).fetchone()
        return self._row(row, "Note not found")

    def update_note(
        self,
        *,
        user_id: str,
        note_id: str,
        body: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        text = body.strip()
        if not text:
            raise ValueError("Note body is required")
        if len(text) > 20000:
            raise ValueError("Note body is too long")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM learning_notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise ReviewNotFound("Note not found")
            if str(row["user_id"]) != user_id:
                raise ReviewConflict("Note belongs to another learner")
            connection.execute(
                "UPDATE learning_notes SET title = ?, body = ?, updated_at = ? WHERE id = ?",
                ((title or "").strip()[:200] or None, text, _iso(), note_id),
            )
            updated = connection.execute("SELECT * FROM learning_notes WHERE id = ?", (note_id,)).fetchone()
        return self._row(updated, "Note not found")

    def delete_note(self, *, user_id: str, note_id: str) -> bool:
        with self.transaction() as connection:
            row = connection.execute("SELECT user_id FROM learning_notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise ReviewNotFound("Note not found")
            if str(row["user_id"]) != user_id:
                raise ReviewConflict("Note belongs to another learner")
            connection.execute("DELETE FROM learning_notes WHERE id = ?", (note_id,))
        return True

    def list_notes(self, *, user_id: str, youtube_video_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        where = "n.user_id = ?"
        if youtube_video_id:
            where += " AND n.youtube_video_id = ?"
            params.append(youtube_video_id)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT n.*, v.title AS video_title
                FROM learning_notes n
                JOIN review_videos v ON v.youtube_video_id = n.youtube_video_id
                WHERE {where}
                ORDER BY n.updated_at DESC
                LIMIT 1000
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_version(self, youtube_video_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM review_subtitle_versions
                WHERE youtube_video_id = ?
                ORDER BY version_number DESC LIMIT 1
                """,
                (youtube_video_id,),
            ).fetchone()
        return dict(row) if row else None

    def artifact_for_video(self, youtube_video_id: str, *, artifact_type: str = "study_pack") -> dict[str, Any] | None:
        latest = self.latest_version(youtube_video_id)
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT a.*, v.title AS video_title
                FROM learning_artifacts a
                JOIN review_videos v ON v.youtube_video_id = a.youtube_video_id
                WHERE a.youtube_video_id = ? AND a.artifact_type = ?
                ORDER BY a.generated_at DESC LIMIT 1
                """,
                (youtube_video_id, artifact_type),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["content"] = self._safe_json(item.pop("content_json"), {})
        item["citations"] = self._safe_json(item.pop("citations_json"), [])
        item["is_stale"] = bool(
            latest is not None and str(item["source_sha256"]) != str(latest["content_sha256"])
        )
        item["latest_subtitle_version"] = int(latest["version_number"]) if latest else None
        return item

    def store_artifact(
        self,
        *,
        youtube_video_id: str,
        subtitle_version_id: str,
        source_sha256: str,
        artifact_type: str,
        title: str,
        content: dict[str, Any],
        citations: list[dict[str, Any]],
        model: str,
        prompt_version: str,
        actor: str,
    ) -> dict[str, Any]:
        artifact_id = uuid.uuid4().hex
        now = _iso()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE learning_artifacts SET status = 'stale' WHERE youtube_video_id = ? AND artifact_type = ?",
                (youtube_video_id, artifact_type),
            )
            existing = connection.execute(
                """
                SELECT id FROM learning_artifacts
                WHERE youtube_video_id = ? AND subtitle_version_id = ?
                  AND artifact_type = ? AND prompt_version = ?
                """,
                (youtube_video_id, subtitle_version_id, artifact_type, prompt_version),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE learning_artifacts
                    SET source_sha256 = ?, title = ?, content_json = ?, citations_json = ?,
                        model = ?, status = 'active', created_by_actor = ?, generated_at = ?
                    WHERE id = ?
                    """,
                    (
                        source_sha256,
                        title,
                        json.dumps(content, ensure_ascii=False, sort_keys=True),
                        json.dumps(citations, ensure_ascii=False, sort_keys=True),
                        model,
                        actor,
                        now,
                        existing["id"],
                    ),
                )
                artifact_id = str(existing["id"])
            else:
                connection.execute(
                    """
                    INSERT INTO learning_artifacts(
                        id, youtube_video_id, subtitle_version_id, source_sha256,
                        artifact_type, title, content_json, citations_json,
                        model, prompt_version, status, created_by_actor, generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        artifact_id,
                        youtube_video_id,
                        subtitle_version_id,
                        source_sha256,
                        artifact_type,
                        title,
                        json.dumps(content, ensure_ascii=False, sort_keys=True),
                        json.dumps(citations, ensure_ascii=False, sort_keys=True),
                        model,
                        prompt_version,
                        actor,
                        now,
                    ),
                )
            row = connection.execute("SELECT * FROM learning_artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._row(row, "Learning artifact not found")

    def begin_generation_job(
        self,
        *,
        youtube_video_id: str,
        subtitle_version_id: str,
        artifact_type: str,
        prompt_version: str,
        model: str,
        actor: str,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with self.transaction() as connection:
            active = connection.execute(
                """
                SELECT * FROM learning_generation_jobs
                WHERE youtube_video_id = ? AND subtitle_version_id = ?
                  AND artifact_type = ? AND prompt_version = ? AND status = 'running'
                ORDER BY started_at DESC LIMIT 1
                """,
                (youtube_video_id, subtitle_version_id, artifact_type, prompt_version),
            ).fetchone()
            if active is not None:
                raise ReviewConflict("A learning-content generation is already running for this version")
            connection.execute(
                """
                INSERT INTO learning_generation_jobs(
                    id, youtube_video_id, subtitle_version_id, artifact_type,
                    prompt_version, model, status, actor, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    job_id,
                    youtube_video_id,
                    subtitle_version_id,
                    artifact_type,
                    prompt_version,
                    model,
                    actor,
                    _iso(),
                ),
            )
            row = connection.execute("SELECT * FROM learning_generation_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row, "Generation job not found")

    def finish_generation_job(self, job_id: str, *, error: str | None = None) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM learning_generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ReviewNotFound("Generation job not found")
            connection.execute(
                """
                UPDATE learning_generation_jobs
                SET status = ?, error = ?, finished_at = ? WHERE id = ?
                """,
                ("failed" if error else "completed", (error or "")[:1000] or None, _iso(), job_id),
            )
            updated = connection.execute("SELECT * FROM learning_generation_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(updated, "Generation job not found")

    def list_generation_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT j.*, v.title AS video_title
                FROM learning_generation_jobs j
                JOIN review_videos v ON v.youtube_video_id = j.youtube_video_id
                ORDER BY j.started_at DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_quiz_attempt(
        self,
        *,
        user_id: str,
        youtube_video_id: str,
        score: int,
        total: int,
        artifact_id: str | None = None,
        answers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if total <= 0 or score < 0 or score > total:
            raise ValueError("Quiz score is invalid")
        attempt_id = uuid.uuid4().hex
        with self.transaction() as connection:
            self._video_exists(connection, youtube_video_id)
            connection.execute(
                """
                INSERT INTO learning_quiz_attempts(
                    id, user_id, youtube_video_id, artifact_id, score,
                    total, answers_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    user_id,
                    youtube_video_id,
                    artifact_id,
                    score,
                    total,
                    json.dumps(answers or {}, ensure_ascii=False, sort_keys=True),
                    _iso(),
                ),
            )
            row = connection.execute("SELECT * FROM learning_quiz_attempts WHERE id = ?", (attempt_id,)).fetchone()
        return self._row(row, "Quiz attempt not found")

    def review_lesson(self, *, user_id: str, youtube_video_id: str) -> dict[str, Any]:
        now_dt = datetime.now(UTC)
        with self.transaction() as connection:
            self._video_exists(connection, youtube_video_id)
            row = connection.execute(
                "SELECT * FROM learning_review_schedule WHERE user_id = ? AND youtube_video_id = ?",
                (user_id, youtube_video_id),
            ).fetchone()
            stage = int(row["stage"]) if row else 0
            next_stage = min(stage + 1, len(self.REVIEW_INTERVALS_DAYS) - 1)
            interval = self.REVIEW_INTERVALS_DAYS[next_stage]
            cycles = int(row["completed_cycles"]) if row else 0
            if stage >= len(self.REVIEW_INTERVALS_DAYS) - 1:
                cycles += 1
            next_due = now_dt + timedelta(days=interval)
            connection.execute(
                """
                INSERT INTO learning_review_schedule(
                    user_id, youtube_video_id, stage, next_due_at,
                    last_reviewed_at, completed_cycles
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, youtube_video_id) DO UPDATE SET
                    stage = excluded.stage,
                    next_due_at = excluded.next_due_at,
                    last_reviewed_at = excluded.last_reviewed_at,
                    completed_cycles = excluded.completed_cycles
                """,
                (user_id, youtube_video_id, next_stage, _iso(next_due), _iso(now_dt), cycles),
            )
            updated = connection.execute(
                "SELECT * FROM learning_review_schedule WHERE user_id = ? AND youtube_video_id = ?",
                (user_id, youtube_video_id),
            ).fetchone()
        return self._row(updated, "Review schedule not found")

    def review_flashcard(
        self,
        *,
        user_id: str,
        artifact_id: str,
        card_key: str,
        rating: FlashcardRating | str,
    ) -> dict[str, Any]:
        normalized = rating.strip().lower()
        if normalized not in self.FLASHCARD_INTERVALS:
            raise ValueError("rating must be again, hard, good or easy")
        key = card_key.strip()[:200]
        if not key:
            raise ValueError("card_key is required")
        interval = int(self.FLASHCARD_INTERVALS[normalized])
        now_dt = datetime.now(UTC)
        with self.transaction() as connection:
            artifact = connection.execute("SELECT 1 FROM learning_artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if artifact is None:
                raise ReviewNotFound("Learning artifact not found")
            connection.execute(
                """
                INSERT INTO learning_flashcard_progress(
                    user_id, artifact_id, card_key, rating, interval_days,
                    next_due_at, last_reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, artifact_id, card_key) DO UPDATE SET
                    rating = excluded.rating,
                    interval_days = excluded.interval_days,
                    next_due_at = excluded.next_due_at,
                    last_reviewed_at = excluded.last_reviewed_at
                """,
                (
                    user_id,
                    artifact_id,
                    key,
                    normalized,
                    interval,
                    _iso(now_dt + timedelta(days=interval)),
                    _iso(now_dt),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM learning_flashcard_progress
                WHERE user_id = ? AND artifact_id = ? AND card_key = ?
                """,
                (user_id, artifact_id, key),
            ).fetchone()
        return self._row(row, "Flashcard progress not found")

    def review_queue(self, *, user_id: str) -> list[dict[str, Any]]:
        now = _iso()
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT r.*, v.title, v.duration_ms,
                       p.last_playback_ms,
                       a.id AS artifact_id,
                       a.generated_at AS artifact_generated_at
                FROM learning_review_schedule r
                JOIN review_videos v ON v.youtube_video_id = r.youtube_video_id
                LEFT JOIN review_video_progress p
                    ON p.user_id = r.user_id AND p.youtube_video_id = r.youtube_video_id
                LEFT JOIN learning_artifacts a
                    ON a.id = (
                        SELECT a2.id FROM learning_artifacts a2
                        WHERE a2.youtube_video_id = r.youtube_video_id
                          AND a2.artifact_type = 'study_pack'
                        ORDER BY a2.generated_at DESC LIMIT 1
                    )
                WHERE r.user_id = ? AND r.next_due_at IS NOT NULL AND r.next_due_at <= ?
                ORDER BY r.next_due_at ASC
                LIMIT 100
                """,
                (user_id, now),
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard(self, *, user_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    v.youtube_video_id,
                    v.title,
                    v.duration_ms,
                    COALESCE(p.last_playback_ms, 0) AS last_playback_ms,
                    COALESCE(p.reviewed_until_ms, 0) AS review_progress_ms,
                    COALESCE(p.completed, 0) AS subtitle_review_completed,
                    COALESCE(s.learning_status,
                        CASE WHEN COALESCE(p.last_playback_ms, 0) > 0 THEN 'in_progress' ELSE 'not_started' END
                    ) AS learning_status,
                    COALESCE(s.saved, 0) AS saved,
                    s.started_at,
                    s.completed_at,
                    COALESCE(s.last_interaction_at, p.updated_at, v.updated_at) AS last_interaction_at,
                    r.next_due_at,
                    r.stage AS review_stage,
                    (SELECT COUNT(*) FROM learning_notes n
                     WHERE n.user_id = ? AND n.youtube_video_id = v.youtube_video_id) AS note_count,
                    (SELECT COUNT(*) FROM learning_bookmarks b
                     WHERE b.user_id = ? AND b.youtube_video_id = v.youtube_video_id) AS bookmark_count,
                    (SELECT COUNT(*) FROM review_suggestions sg
                     JOIN review_subtitle_segments seg ON seg.id = sg.segment_id
                     WHERE sg.user_id = ? AND seg.youtube_video_id = v.youtube_video_id) AS suggestion_count,
                    (SELECT a.generated_at FROM learning_artifacts a
                     WHERE a.youtube_video_id = v.youtube_video_id AND a.artifact_type = 'study_pack'
                     ORDER BY a.generated_at DESC LIMIT 1) AS artifact_generated_at
                FROM review_videos v
                LEFT JOIN review_video_progress p
                    ON p.user_id = ? AND p.youtube_video_id = v.youtube_video_id
                LEFT JOIN learning_video_state s
                    ON s.user_id = ? AND s.youtube_video_id = v.youtube_video_id
                LEFT JOIN learning_review_schedule r
                    ON r.user_id = ? AND r.youtube_video_id = v.youtube_video_id
                ORDER BY COALESCE(s.last_interaction_at, p.updated_at, v.updated_at) DESC, v.title
                """,
                (user_id, user_id, user_id, user_id, user_id, user_id),
            ).fetchall()
        videos: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            duration = int(item.get("duration_ms") or 0)
            playback = int(item.get("last_playback_ms") or 0)
            item["watch_percent"] = min(100, round(playback * 100 / duration)) if duration > 0 else 0
            videos.append(item)
        completed = sum(item["learning_status"] == "completed" for item in videos)
        in_progress = sum(item["learning_status"] == "in_progress" for item in videos)
        due = self.review_queue(user_id=user_id)
        continue_video = next(
            (item for item in videos if item["learning_status"] == "in_progress"),
            next((item for item in videos if int(item.get("last_playback_ms") or 0) > 0), None),
        )
        return {
            "summary": {
                "video_count": len(videos),
                "completed_count": completed,
                "in_progress_count": in_progress,
                "not_started_count": max(0, len(videos) - completed - in_progress),
                "review_due_count": len(due),
                "saved_count": sum(bool(item.get("saved")) for item in videos),
            },
            "continue_learning": continue_video,
            "videos": videos,
            "review_due": due,
            "recent_notes": self.list_notes(user_id=user_id)[:5],
            "recent_bookmarks": self.list_bookmarks(user_id=user_id)[:5],
        }

    def lesson(self, *, user_id: str, youtube_video_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            video = connection.execute(
                "SELECT * FROM review_videos WHERE youtube_video_id = ?", (youtube_video_id,)
            ).fetchone()
            if video is None:
                raise ReviewNotFound("Learning video not found")
            progress = connection.execute(
                "SELECT * FROM review_video_progress WHERE user_id = ? AND youtube_video_id = ?",
                (user_id, youtube_video_id),
            ).fetchone()
            state = connection.execute(
                "SELECT * FROM learning_video_state WHERE user_id = ? AND youtube_video_id = ?",
                (user_id, youtube_video_id),
            ).fetchone()
            segments = connection.execute(
                """
                SELECT
                    seg.id,
                    seg.segment_index,
                    seg.start_ms,
                    seg.end_ms,
                    seg.working_text,
                    suggestion.id AS my_suggestion_id,
                    suggestion.suggested_text AS my_suggested_text,
                    suggestion.status AS my_suggestion_status,
                    CASE WHEN EXISTS (
                        SELECT 1
                        FROM review_suggestion_events event
                        WHERE event.suggestion_id = suggestion.id
                          AND event.event_type = 'withdrawn'
                    ) THEN 1 ELSE 0 END AS my_suggestion_withdrawn
                FROM review_subtitle_segments seg
                LEFT JOIN review_suggestions suggestion
                    ON suggestion.id = (
                        SELECT latest.id
                        FROM review_suggestions latest
                        WHERE latest.segment_id = seg.id AND latest.user_id = ?
                        ORDER BY latest.updated_at DESC, latest.created_at DESC
                        LIMIT 1
                    )
                WHERE seg.youtube_video_id = ?
                ORDER BY seg.segment_index
                """,
                (user_id, youtube_video_id),
            ).fetchall()
            schedule = connection.execute(
                "SELECT * FROM learning_review_schedule WHERE user_id = ? AND youtube_video_id = ?",
                (user_id, youtube_video_id),
            ).fetchone()
            attempts = connection.execute(
                """
                SELECT id, score, total, created_at FROM learning_quiz_attempts
                WHERE user_id = ? AND youtube_video_id = ?
                ORDER BY created_at DESC LIMIT 10
                """,
                (user_id, youtube_video_id),
            ).fetchall()
        return {
            "video": dict(video),
            "progress": dict(progress) if progress else None,
            "learning_state": dict(state) if state else {
                "learning_status": "in_progress" if progress and int(progress["last_playback_ms"]) > 0 else "not_started",
                "saved": 0,
            },
            "segments": [dict(row) for row in segments],
            "artifact": self.artifact_for_video(youtube_video_id),
            "notes": self.list_notes(user_id=user_id, youtube_video_id=youtube_video_id),
            "bookmarks": self.list_bookmarks(user_id=user_id, youtube_video_id=youtube_video_id),
            "review_schedule": dict(schedule) if schedule else None,
            "quiz_attempts": [dict(row) for row in attempts],
        }

    def search(self, *, user_id: str, query: str, limit: int = 40) -> dict[str, Any]:
        del user_id  # Reserved for future personalized ranking/access policy.
        text = query.strip()
        if len(text) < 2:
            raise ValueError("Search query must contain at least two characters")
        cap = max(1, min(limit, 100))
        pattern = f"%{text.replace('%', '\\%').replace('_', '\\_')}%"
        with closing(self.connect()) as connection:
            segment_rows = connection.execute(
                """
                SELECT seg.id AS segment_id, seg.youtube_video_id, seg.segment_index,
                       seg.start_ms, seg.end_ms, seg.working_text AS text,
                       v.title AS video_title
                FROM review_subtitle_segments seg
                JOIN review_videos v ON v.youtube_video_id = seg.youtube_video_id
                WHERE seg.working_text LIKE ? ESCAPE '\\'
                ORDER BY v.title, seg.segment_index
                LIMIT ?
                """,
                (pattern, cap),
            ).fetchall()
            artifact_rows = connection.execute(
                """
                SELECT a.id, a.youtube_video_id, a.title, a.artifact_type,
                       a.content_json, a.citations_json, a.generated_at,
                       v.title AS video_title
                FROM learning_artifacts a
                JOIN review_videos v ON v.youtube_video_id = a.youtube_video_id
                WHERE a.content_json LIKE ? ESCAPE '\\'
                ORDER BY a.generated_at DESC LIMIT ?
                """,
                (pattern, min(20, cap)),
            ).fetchall()
        artifacts: list[dict[str, Any]] = []
        for row in artifact_rows:
            item = dict(row)
            content = self._safe_json(item.pop("content_json"), {})
            citations = self._safe_json(item.pop("citations_json"), [])
            first = citations[0] if isinstance(citations, list) and citations else {}
            item["start_ms"] = int(first.get("start_ms") or 0) if isinstance(first, dict) else 0
            serialized = json.dumps(content, ensure_ascii=False)
            pos = serialized.find(text)
            if pos >= 0:
                item["snippet"] = serialized[max(0, pos - 80): pos + len(text) + 120]
            else:
                item["snippet"] = str(item["title"])
            artifacts.append(item)
        return {
            "query": text,
            "subtitle_results": [dict(row) for row in segment_rows],
            "artifact_results": artifacts,
        }
