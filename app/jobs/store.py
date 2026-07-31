"""SQLite-backed job state, leases, events, and cost reservations."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


ACTIVE_STATUSES = frozenset(
    {
        "preflight",
        "awaiting_confirmation",
        "queued",
        "downloading",
        "normalizing",
        "transcribing",
        "correcting",
    }
)
JOB_ID_SAFE = re.compile(r"[^a-z0-9]+")


class JobNotFound(LookupError):
    pass


class JobConflict(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def _job_id(source_name: str) -> str:
    stem = Path(source_name).stem.lower()
    slug = JOB_ID_SAFE.sub("-", stem).strip("-")[:48] or "transcript"
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    return f"{slug}-{stamp}-{uuid.uuid4().hex[:6]}"


def _batch_id() -> str:
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    return f"batch-{stamp}-{uuid.uuid4().hex[:6]}"


class JobStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
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
                CREATE TABLE IF NOT EXISTS batch_previews (
                    id TEXT PRIMARY KEY,
                    selection_mode TEXT NOT NULL
                        CHECK(selection_mode IN ('files', 'folder')),
                    source_root TEXT,
                    item_count INTEGER NOT NULL CHECK(item_count > 0),
                    total_size_bytes INTEGER NOT NULL CHECK(total_size_bytes > 0),
                    inspected_by TEXT NOT NULL,
                    inspected_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS source_previews (
                    id TEXT PRIMARY KEY,
                    batch_preview_id TEXT REFERENCES batch_previews(id),
                    item_index INTEGER NOT NULL DEFAULT 0,
                    source_path TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
                    modified_at TEXT,
                    mime_type TEXT,
                    inspected_by TEXT NOT NULL,
                    inspected_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    batch_preview_id TEXT NOT NULL UNIQUE
                        REFERENCES batch_previews(id),
                    name TEXT NOT NULL,
                    selection_mode TEXT NOT NULL,
                    source_root TEXT,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd TEXT,
                    reserved_cost_usd TEXT NOT NULL DEFAULT '0',
                    actual_cost_usd TEXT NOT NULL DEFAULT '0',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    preview_id TEXT NOT NULL UNIQUE REFERENCES source_previews(id),
                    batch_id TEXT REFERENCES batches(id),
                    queue_position INTEGER NOT NULL DEFAULT 0,
                    source_path TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_size_bytes INTEGER NOT NULL,
                    language_code TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    enable_gemini_correction INTEGER NOT NULL,
                    enable_subtitles INTEGER NOT NULL,
                    require_human_review INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    active_stage TEXT,
                    stage_detail TEXT,
                    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
                    error TEXT,
                    duration_seconds REAL,
                    source_checksum TEXT,
                    media_format TEXT,
                    audio_codec TEXT,
                    estimated_cost_usd TEXT,
                    reserved_cost_usd TEXT NOT NULL DEFAULT '0',
                    actual_cost_usd TEXT NOT NULL DEFAULT '0',
                    pricing_version TEXT,
                    approved_by TEXT,
                    approved_at TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    locked_by TEXT,
                    lease_expires_at TEXT,
                    last_heartbeat_at TEXT
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS stage_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    input_checksum TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT,
                    UNIQUE(job_id, stage)
                );

                CREATE TABLE IF NOT EXISTS usage_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_units INTEGER,
                    output_units INTEGER,
                    estimated_cost_usd TEXT NOT NULL,
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
                CREATE INDEX IF NOT EXISTS events_job_idx ON job_events(job_id, id);
                CREATE INDEX IF NOT EXISTS usage_job_idx ON usage_records(job_id, id);
                """
            )
            self._ensure_column(
                connection,
                "source_previews",
                "batch_preview_id",
                "TEXT REFERENCES batch_previews(id)",
            )
            self._ensure_column(
                connection,
                "source_previews",
                "item_index",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "jobs",
                "batch_id",
                "TEXT REFERENCES batches(id)",
            )
            self._ensure_column(
                connection,
                "jobs",
                "queue_position",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(connection, "jobs", "source_checksum", "TEXT")
            self._ensure_column(connection, "jobs", "media_format", "TEXT")
            self._ensure_column(connection, "jobs", "audio_codec", "TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS jobs_batch_idx
                ON jobs(batch_id, queue_position)
                """
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_preview(
        self,
        *,
        source_path: str,
        source_name: str,
        size_bytes: int,
        modified_at: str | None,
        mime_type: str | None,
        actor: str,
        ttl_minutes: int = 30,
    ) -> dict[str, Any]:
        preview_id = uuid.uuid4().hex
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_previews(
                    id, source_path, source_name, size_bytes, modified_at,
                    mime_type, inspected_by, inspected_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    source_path,
                    source_name,
                    size_bytes,
                    modified_at,
                    mime_type,
                    actor,
                    _iso(now),
                    _iso(now + timedelta(minutes=ttl_minutes)),
                ),
            )
        return self.get_preview(preview_id)

    def get_preview(self, preview_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM source_previews WHERE id = ?", (preview_id,)
            ).fetchone()
        if row is None:
            raise JobNotFound("Source preview not found")
        return dict(row)

    def create_batch_preview(
        self,
        *,
        selection_mode: str,
        source_root: str | None,
        items: list[dict[str, Any]],
        actor: str,
        ttl_minutes: int = 30,
    ) -> dict[str, Any]:
        if selection_mode not in {"files", "folder"} or not items:
            raise ValueError("Invalid batch preview")
        preview_id = uuid.uuid4().hex
        now = utc_now()
        total_size = sum(int(item["size_bytes"]) for item in items)
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO batch_previews(
                    id, selection_mode, source_root, item_count,
                    total_size_bytes, inspected_by, inspected_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    selection_mode,
                    source_root,
                    len(items),
                    total_size,
                    actor,
                    _iso(now),
                    _iso(now + timedelta(minutes=ttl_minutes)),
                ),
            )
            for index, item in enumerate(items):
                connection.execute(
                    """
                    INSERT INTO source_previews(
                        id, batch_preview_id, item_index, source_path,
                        source_name, size_bytes, modified_at, mime_type,
                        inspected_by, inspected_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        preview_id,
                        index,
                        item["source_path"],
                        item["name"],
                        int(item["size_bytes"]),
                        item.get("modified_at"),
                        item.get("mime_type"),
                        actor,
                        _iso(now),
                        _iso(now + timedelta(minutes=ttl_minutes)),
                    ),
                )
        return self.get_batch_preview(preview_id)

    def get_batch_preview(self, preview_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM batch_previews WHERE id = ?", (preview_id,)
            ).fetchone()
            items = connection.execute(
                """
                SELECT id, item_index, source_path, source_name, size_bytes,
                       modified_at, mime_type
                FROM source_previews
                WHERE batch_preview_id = ?
                ORDER BY item_index
                """,
                (preview_id,),
            ).fetchall()
        if row is None:
            raise JobNotFound("Batch preview not found")
        result = dict(row)
        result["items"] = [dict(item) for item in items]
        return result

    def create_preflight_batch(
        self,
        *,
        batch_preview_id: str,
        language_code: str,
        profile: str,
        enable_gemini_correction: bool,
        enable_subtitles: bool,
        require_human_review: bool,
        actor: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            preview = connection.execute(
                "SELECT * FROM batch_previews WHERE id = ?", (batch_preview_id,)
            ).fetchone()
            if preview is None:
                raise JobNotFound("Batch preview not found")
            if preview["consumed_at"]:
                raise JobConflict("此批次預覽已建立過任務")
            if datetime.fromisoformat(preview["expires_at"]) <= utc_now():
                raise JobConflict("批次預覽已過期，請重新檢查")
            items = connection.execute(
                """
                SELECT * FROM source_previews
                WHERE batch_preview_id = ?
                ORDER BY item_index
                """,
                (batch_preview_id,),
            ).fetchall()
            if not items:
                raise JobConflict("批次預覽沒有可建立的影音檔")

            batch_id = _batch_id()
            batch_name = (
                Path(preview["source_root"] or "").name
                if preview["selection_mode"] == "folder"
                else f"{len(items)} 個影音檔"
            ) or "Drive 批次"
            connection.execute(
                """
                INSERT INTO batches(
                    id, batch_preview_id, name, selection_mode, source_root,
                    status, item_count, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'preflight', ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    batch_preview_id,
                    batch_name,
                    preview["selection_mode"],
                    preview["source_root"],
                    len(items),
                    actor,
                    now,
                    now,
                ),
            )
            job_ids: list[str] = []
            for position, item in enumerate(items):
                job_id = _job_id(item["source_name"])
                job_ids.append(job_id)
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, preview_id, batch_id, queue_position, source_path,
                        source_name, source_size_bytes, language_code, profile,
                        enable_gemini_correction, enable_subtitles,
                        require_human_review, status, active_stage, stage_detail,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'preflight', 'source', '等待安全下載與媒體檢查',
                        ?, ?, ?)
                    """,
                    (
                        job_id,
                        item["id"],
                        batch_id,
                        position,
                        item["source_path"],
                        item["source_name"],
                        item["size_bytes"],
                        language_code,
                        profile,
                        int(enable_gemini_correction),
                        int(enable_subtitles),
                        int(require_human_review),
                        actor,
                        now,
                        now,
                    ),
                )
                self._event(
                    connection,
                    job_id,
                    "job_preflight_created",
                    actor,
                    {"status": "preflight", "batch_id": batch_id},
                )
            connection.execute(
                "UPDATE source_previews SET consumed_at = ? WHERE batch_preview_id = ?",
                (now, batch_preview_id),
            )
            connection.execute(
                "UPDATE batch_previews SET consumed_at = ? WHERE id = ?",
                (now, batch_preview_id),
            )
        return {
            "batch": self.get_batch(batch_id),
            "jobs": [self.get_job(job_id) for job_id in job_ids],
        }

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
            jobs = connection.execute(
                """
                SELECT * FROM jobs WHERE batch_id = ?
                ORDER BY queue_position
                """,
                (batch_id,),
            ).fetchall()
        if row is None:
            raise JobNotFound("Batch not found")
        result = dict(row)
        result["jobs"] = [dict(job) for job in jobs]
        return result

    def list_batches(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM batches ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_preflight_job(
        self,
        *,
        preview_id: str,
        language_code: str,
        profile: str,
        enable_gemini_correction: bool,
        enable_subtitles: bool,
        require_human_review: bool,
        actor: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            active = connection.execute(
                f"SELECT id FROM jobs WHERE status IN ({','.join('?' for _ in ACTIVE_STATUSES)}) LIMIT 1",
                tuple(ACTIVE_STATUSES),
            ).fetchone()
            if active is not None:
                raise JobConflict(f"已有進行中任務：{active['id']}")
            preview = connection.execute(
                "SELECT * FROM source_previews WHERE id = ?", (preview_id,)
            ).fetchone()
            if preview is None:
                raise JobNotFound("Source preview not found")
            if preview["consumed_at"]:
                raise JobConflict("來源預覽已建立過任務")
            if datetime.fromisoformat(preview["expires_at"]) <= utc_now():
                raise JobConflict("來源預覽已過期，請重新檢查")

            job_id = _job_id(preview["source_name"])
            connection.execute(
                """
                INSERT INTO jobs(
                    id, preview_id, source_path, source_name, source_size_bytes,
                    language_code, profile, enable_gemini_correction,
                    enable_subtitles, require_human_review, status, active_stage,
                    stage_detail, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preflight',
                    'source', '等待安全下載與媒體檢查', ?, ?, ?)
                """,
                (
                    job_id,
                    preview_id,
                    preview["source_path"],
                    preview["source_name"],
                    preview["size_bytes"],
                    language_code,
                    profile,
                    int(enable_gemini_correction),
                    int(enable_subtitles),
                    int(require_human_review),
                    actor,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE source_previews SET consumed_at = ? WHERE id = ?",
                (now, preview_id),
            )
            self._event(
                connection,
                job_id,
                "job_preflight_created",
                actor,
                {"status": "preflight"},
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFound("Job not found")
        return dict(row)

    def list_jobs(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_cost_estimate(
        self,
        *,
        job_id: str,
        duration_seconds: float,
        estimated_cost_usd: Decimal,
        pricing_version: str,
        worker_id: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            self._require_lease(connection, job_id, worker_id)
            updated = connection.execute(
                """
                UPDATE jobs
                SET duration_seconds = ?, estimated_cost_usd = ?,
                    pricing_version = ?, status = 'awaiting_confirmation',
                    active_stage = 'cost_confirmation',
                    stage_detail = '等待人工確認預估費用',
                    progress = 8, updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (
                    duration_seconds,
                    str(estimated_cost_usd),
                    pricing_version,
                    now,
                    job_id,
                ),
            )
            if updated.rowcount != 1:
                raise JobNotFound("Job not found")
            self._clear_lease(connection, job_id, worker_id)
            self._event(
                connection,
                job_id,
                "cost_estimated",
                worker_id,
                {
                    "duration_seconds": duration_seconds,
                    "estimated_cost_usd": str(estimated_cost_usd),
                    "pricing_version": pricing_version,
                },
            )
        return self.get_job(job_id)

    def record_preflight_result(
        self,
        *,
        job_id: str,
        duration_seconds: float,
        source_checksum: str,
        media_format: str | None,
        audio_codec: str | None,
        estimated_cost_usd: Decimal,
        pricing_version: str,
        worker_id: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            self._require_lease(connection, job_id, worker_id)
            row = connection.execute(
                "SELECT batch_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFound("Job not found")
            connection.execute(
                """
                UPDATE jobs
                SET duration_seconds = ?, source_checksum = ?,
                    media_format = ?, audio_codec = ?,
                    estimated_cost_usd = ?, pricing_version = ?,
                    status = 'awaiting_confirmation',
                    active_stage = 'cost_confirmation',
                    stage_detail = '等待人工確認預估費用',
                    progress = 8, updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (
                    duration_seconds,
                    source_checksum,
                    media_format,
                    audio_codec,
                    str(estimated_cost_usd),
                    pricing_version,
                    now,
                    job_id,
                ),
            )
            self._clear_lease(connection, job_id, worker_id)
            self._event(
                connection,
                job_id,
                "preflight_completed",
                worker_id,
                {
                    "duration_seconds": duration_seconds,
                    "source_checksum": source_checksum,
                    "media_format": media_format,
                    "audio_codec": audio_codec,
                    "estimated_cost_usd": str(estimated_cost_usd),
                    "pricing_version": pricing_version,
                },
            )
            if row["batch_id"]:
                self._refresh_batch_estimate(connection, row["batch_id"], now)
        return self.get_job(job_id)

    def fail_job(
        self,
        *,
        job_id: str,
        stage: str,
        error: str,
        worker_id: str,
    ) -> dict[str, Any]:
        safe_error = error[:1000]
        now = _iso()
        with self.transaction() as connection:
            self._require_lease(connection, job_id, worker_id)
            row = connection.execute(
                "SELECT batch_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFound("Job not found")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', active_stage = ?, stage_detail = ?,
                    error = ?, updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (stage, "本機媒體檢查失敗", safe_error, now, job_id),
            )
            self._clear_lease(connection, job_id, worker_id)
            self._event(
                connection,
                job_id,
                "job_failed",
                worker_id,
                {"stage": stage, "error": safe_error},
            )
            if row["batch_id"]:
                connection.execute(
                    """
                    UPDATE batches
                    SET failed_count = (
                        SELECT COUNT(*) FROM jobs
                        WHERE batch_id = ? AND status = 'failed'
                    ), updated_at = ?, revision = revision + 1
                    WHERE id = ?
                    """,
                    (row["batch_id"], now, row["batch_id"]),
                )
                self._refresh_batch_estimate(connection, row["batch_id"], now)
        return self.get_job(job_id)

    def next_job_for_status(self, status: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at, batch_id, queue_position
                LIMIT 1
                """,
                (status,),
            ).fetchone()
        return dict(row) if row is not None else None

    def approve_batch(
        self,
        *,
        batch_id: str,
        expected_revision: int,
        confirmed_estimated_cost_usd: Decimal,
        project_limit_usd: Decimal,
        actor: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise JobNotFound("Batch not found")
            if batch["status"] != "awaiting_confirmation":
                raise JobConflict("此批次目前不接受費用確認")
            if batch["revision"] != expected_revision:
                raise JobConflict("批次已更新，請重新載入後再確認")
            estimate = Decimal(batch["estimated_cost_usd"] or "0")
            if estimate <= 0 or estimate != confirmed_estimated_cost_usd:
                raise JobConflict("確認金額與最新批次預估費用不一致")
            committed = self._committed_cost(
                connection, exclude_batch_id=batch_id
            )
            if committed + estimate > project_limit_usd:
                raise JobConflict("確認後將超過 US$200 專案預估成本上限")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', active_stage = 'queued',
                    stage_detail = '批次費用已確認，依序等待 Worker',
                    reserved_cost_usd = estimated_cost_usd,
                    approved_by = ?, approved_at = ?,
                    updated_at = ?, revision = revision + 1
                WHERE batch_id = ? AND status = 'awaiting_confirmation'
                """,
                (actor, now, now, batch_id),
            )
            connection.execute(
                """
                UPDATE batches
                SET status = 'queued', reserved_cost_usd = ?,
                    updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (str(estimate), now, batch_id),
            )
            for row in connection.execute(
                "SELECT id FROM jobs WHERE batch_id = ?", (batch_id,)
            ).fetchall():
                self._event(
                    connection,
                    row["id"],
                    "batch_cost_approved",
                    actor,
                    {"batch_id": batch_id, "reserved_cost_usd": str(estimate)},
                )
        return self.get_batch(batch_id)

    def approve_job(
        self,
        *,
        job_id: str,
        expected_revision: int,
        confirmed_estimated_cost_usd: Decimal,
        project_limit_usd: Decimal,
        actor: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFound("Job not found")
            if row["status"] != "awaiting_confirmation":
                raise JobConflict("任務目前不接受費用確認")
            if row["revision"] != expected_revision:
                raise JobConflict("任務已更新，請重新載入後再確認")
            estimate = Decimal(row["estimated_cost_usd"] or "0")
            if estimate <= 0 or estimate != confirmed_estimated_cost_usd:
                raise JobConflict("確認金額與最新預估費用不一致")
            committed = self._committed_cost(connection, exclude_job_id=job_id)
            if committed + estimate > project_limit_usd:
                raise JobConflict("確認後將超過 US$200 專案預估成本上限")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', active_stage = 'queued',
                    stage_detail = '已確認費用，等待 Worker',
                    reserved_cost_usd = ?, approved_by = ?, approved_at = ?,
                    updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (str(estimate), actor, now, now, job_id),
            )
            self._event(
                connection,
                job_id,
                "job_cost_approved",
                actor,
                {"reserved_cost_usd": str(estimate)},
            )
        return self.get_job(job_id)

    def acquire_lease(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        now = utc_now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.transaction() as connection:
            other = connection.execute(
                """
                SELECT id FROM jobs
                WHERE id != ? AND locked_by IS NOT NULL
                  AND lease_expires_at > ?
                LIMIT 1
                """,
                (job_id, _iso(now)),
            ).fetchone()
            if other is not None:
                raise JobConflict(f"目前只允許一個來源檔執行：{other['id']}")
            row = connection.execute(
                "SELECT locked_by, lease_expires_at FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFound("Job not found")
            lease_expired = (
                not row["lease_expires_at"]
                or datetime.fromisoformat(row["lease_expires_at"]) <= now
            )
            if row["locked_by"] not in {None, worker_id} and not lease_expired:
                raise JobConflict("任務已由其他 Worker 鎖定")
            connection.execute(
                """
                UPDATE jobs
                SET locked_by = ?, lease_expires_at = ?,
                    last_heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_id, _iso(expires), _iso(now), _iso(now), job_id),
            )
        return self.get_job(job_id)

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, last_heartbeat_at = ?, updated_at = ?
                WHERE id = ? AND locked_by = ? AND lease_expires_at > ?
                """,
                (
                    _iso(now + timedelta(seconds=lease_seconds)),
                    _iso(now),
                    _iso(now),
                    job_id,
                    worker_id,
                    _iso(now),
                ),
            )
            if updated.rowcount != 1:
                raise JobConflict("Worker lease 不存在或已過期")
        return self.get_job(job_id)

    def release_lease(self, job_id: str, worker_id: str) -> None:
        with self.transaction() as connection:
            self._clear_lease(connection, job_id, worker_id)

    def cost_summary(self, project_limit_usd: Decimal) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            committed = self._committed_cost(connection)
            actual = sum(
                (
                    Decimal(row["actual_cost_usd"] or "0")
                    for row in connection.execute(
                        "SELECT actual_cost_usd FROM jobs"
                    ).fetchall()
                ),
                Decimal("0"),
            )
        return {
            "project_limit_usd": str(project_limit_usd),
            "committed_estimated_cost_usd": str(committed),
            "recorded_actual_cost_usd": str(actual),
            "remaining_estimated_budget_usd": str(
                max(Decimal("0"), project_limit_usd - committed)
            ),
            "accounting_note": (
                "程式僅記錄預估與用量；Cloud Billing 才是實際帳務依據。"
            ),
        }

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_events(job_id, event_type, actor, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                actor,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                _iso(),
            ),
        )

    @staticmethod
    def _committed_cost(
        connection: sqlite3.Connection,
        *,
        exclude_job_id: str | None = None,
        exclude_batch_id: str | None = None,
    ) -> Decimal:
        rows = connection.execute(
            "SELECT id, batch_id, reserved_cost_usd, actual_cost_usd FROM jobs"
        ).fetchall()
        return sum(
            (
                max(
                    Decimal(row["reserved_cost_usd"] or "0"),
                    Decimal(row["actual_cost_usd"] or "0"),
                )
                for row in rows
                if (exclude_job_id is None or row["id"] != exclude_job_id)
                and (
                    exclude_batch_id is None
                    or row["batch_id"] != exclude_batch_id
                )
            ),
            Decimal("0"),
        )

    @staticmethod
    def _refresh_batch_estimate(
        connection: sqlite3.Connection,
        batch_id: str,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT status, estimated_cost_usd
            FROM jobs WHERE batch_id = ?
            """,
            (batch_id,),
        ).fetchall()
        if not rows or any(
            row["status"] not in {"awaiting_confirmation", "failed"} for row in rows
        ):
            return
        failed = sum(row["status"] == "failed" for row in rows)
        estimates = [
            Decimal(row["estimated_cost_usd"])
            for row in rows
            if row["status"] == "awaiting_confirmation"
            and row["estimated_cost_usd"]
        ]
        if len(estimates) + failed != len(rows):
            return
        total = sum(estimates, Decimal("0"))
        status = "awaiting_confirmation" if estimates else "failed"
        connection.execute(
            """
            UPDATE batches
            SET status = ?, estimated_cost_usd = ?, failed_count = ?,
                updated_at = ?, revision = revision + 1
            WHERE id = ?
            """,
            (status, str(total), failed, now, batch_id),
        )

    @staticmethod
    def _require_lease(
        connection: sqlite3.Connection,
        job_id: str,
        worker_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT locked_by, lease_expires_at FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobNotFound("Job not found")
        if (
            row["locked_by"] != worker_id
            or not row["lease_expires_at"]
            or datetime.fromisoformat(row["lease_expires_at"]) <= utc_now()
        ):
            raise JobConflict("有效的 Worker lease 為必要條件")

    @staticmethod
    def _clear_lease(
        connection: sqlite3.Connection,
        job_id: str,
        worker_id: str,
    ) -> None:
        updated = connection.execute(
            """
            UPDATE jobs
            SET locked_by = NULL, lease_expires_at = NULL
            WHERE id = ? AND locked_by = ?
            """,
            (job_id, worker_id),
        )
        if updated.rowcount != 1:
            raise JobConflict("無法釋放不屬於此 Worker 的 lease")
