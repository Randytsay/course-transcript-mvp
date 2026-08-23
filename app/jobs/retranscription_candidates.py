"""Durable state for paid single-chunk ASR retranscription candidates.

Candidates are deliberately independent from the source job state. A completed
or awaiting-review job stays untouched while a candidate is queued, submitted,
recovered, compared, or rejected. Applying a candidate is a later explicit
operation and is not performed by this module.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.jobs.store import JobConflict, JobNotFound, JobStore


ACTIVE_STATUSES = {"queued", "submitted", "processing"}
TERMINAL_STATUSES = {"completed", "failed", "rejected", "applied", "stale"}
RECOGNIZER = "chirp_3"
CONFIG_VERSION = "chirp3-candidate-v1"


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def chunk_source_sha256(job_dir: Path, chunk_index: int) -> str:
    """Hash the accepted chunk evidence used to detect stale candidates."""
    chunk = Path(job_dir) / "chunks" / f"chunk-{chunk_index:03d}"
    digest = hashlib.sha256()
    found = False
    for name in ("manifest.json", "words.json", "partial-transcript.json"):
        path = chunk / name
        if not path.is_file():
            continue
        found = True
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if not found:
        raise JobConflict(f"chunk-{chunk_index:03d} 缺少可建立候選的 ASR 證據")
    return digest.hexdigest()


def config_sha256(*, language_code: str, processing_strategy: str) -> str:
    payload = json.dumps(
        {
            "version": CONFIG_VERSION,
            "recognizer": RECOGNIZER,
            "language_code": language_code,
            "processing_strategy": processing_strategy,
            "word_time_offsets": True,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


def request_idempotency_key(
    *,
    job_id: str,
    source_revision: int,
    chunk_index: int,
    source_chunk_sha256: str,
    recognizer_config_sha256: str,
) -> str:
    payload = "|".join(
        (
            job_id,
            str(source_revision),
            str(chunk_index),
            source_chunk_sha256,
            RECOGNIZER,
            recognizer_config_sha256,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RetranscriptionCandidateStore:
    def __init__(self, jobs: JobStore):
        self.jobs = jobs
        self.initialize()

    def initialize(self) -> None:
        with self.jobs.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS asr_retranscription_candidates (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    source_revision INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    source_chunk_sha256 TEXT NOT NULL,
                    source_audio_sha256 TEXT NOT NULL DEFAULT '',
                    recognizer TEXT NOT NULL,
                    recognizer_config_sha256 TEXT NOT NULL,
                    language_code TEXT NOT NULL,
                    processing_strategy TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    candidate_relpath TEXT NOT NULL,
                    estimated_cost_usd TEXT NOT NULL,
                    confirmed_cost_usd TEXT NOT NULL,
                    pricing_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submitted_at TEXT,
                    completed_at TEXT,
                    failed_at TEXT,
                    rejected_at TEXT,
                    rejected_by TEXT,
                    operation_name TEXT,
                    error_kind TEXT,
                    error_safe_message TEXT,
                    locked_by TEXT,
                    lease_expires_at TEXT,
                    lease_generation INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS asr_retx_job_idx
                ON asr_retranscription_candidates(job_id, requested_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS asr_retx_status_idx
                ON asr_retranscription_candidates(status, lease_expires_at, requested_at)
                """
            )

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        if row is None:
            raise JobNotFound("Retranscription candidate not found")
        return dict(row)

    def get(self, candidate_id: str) -> dict[str, Any]:
        with self.jobs.connect() as connection:
            row = connection.execute(
                "SELECT * FROM asr_retranscription_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        return self._row(row)

    def list_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.jobs.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM asr_retranscription_candidates
                WHERE job_id=? ORDER BY requested_at DESC
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create(
        self,
        *,
        job_id: str,
        expected_revision: int,
        chunk_index: int,
        source_chunk_sha256: str,
        language_code: str,
        processing_strategy: str,
        estimated_cost_usd: Decimal,
        confirmed_cost_usd: Decimal,
        pricing_version: str,
        actor: str,
    ) -> tuple[dict[str, Any], bool]:
        if confirmed_cost_usd != estimated_cost_usd:
            raise JobConflict(
                "重辨識費用確認值已變動；請重新載入最新估價後再確認"
            )
        config_digest = config_sha256(
            language_code=language_code,
            processing_strategy=processing_strategy,
        )
        key = request_idempotency_key(
            job_id=job_id,
            source_revision=expected_revision,
            chunk_index=chunk_index,
            source_chunk_sha256=source_chunk_sha256,
            recognizer_config_sha256=config_digest,
        )
        now = _iso()
        candidate_id = uuid.uuid4().hex
        relpath = f"retranscription-candidates/{candidate_id}"
        with self.jobs.transaction() as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise JobNotFound(job_id)
            if int(job["revision"]) != int(expected_revision):
                raise JobConflict("任務版本已更新；請重新載入後再建立重辨識候選")
            if str(job["status"]) not in {"completed", "awaiting_review", "failed"}:
                raise JobConflict("只有已完成、待人工審查或失敗的任務可建立單段重辨識候選")
            existing = connection.execute(
                "SELECT * FROM asr_retranscription_candidates WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                return dict(existing), False
            connection.execute(
                """
                INSERT INTO asr_retranscription_candidates (
                    id, job_id, source_revision, chunk_index,
                    source_chunk_sha256, recognizer, recognizer_config_sha256,
                    language_code, processing_strategy, idempotency_key,
                    candidate_relpath, estimated_cost_usd, confirmed_cost_usd,
                    pricing_version, status, requested_by, requested_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    candidate_id,
                    job_id,
                    int(expected_revision),
                    int(chunk_index),
                    source_chunk_sha256,
                    RECOGNIZER,
                    config_digest,
                    language_code,
                    processing_strategy,
                    key,
                    relpath,
                    str(estimated_cost_usd),
                    str(confirmed_cost_usd),
                    pricing_version,
                    actor,
                    now,
                    now,
                ),
            )
            self.jobs._event(
                connection,
                job_id,
                "asr_retranscription_candidate_requested",
                actor,
                {
                    "candidate_id": candidate_id,
                    "chunk_index": int(chunk_index),
                    "source_revision": int(expected_revision),
                    "estimated_cost_usd": str(estimated_cost_usd),
                    "processing_strategy": processing_strategy,
                },
            )
            row = connection.execute(
                "SELECT * FROM asr_retranscription_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        return self._row(row), True

    def acquire_next(self, worker_id: str, *, lease_seconds: int = 300) -> dict[str, Any] | None:
        now_dt = datetime.now(UTC)
        now = _iso(now_dt)
        expires = _iso(now_dt + timedelta(seconds=max(30, lease_seconds)))
        with self.jobs.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM asr_retranscription_candidates
                WHERE status IN ('queued','submitted','processing')
                  AND (locked_by IS NULL OR lease_expires_at IS NULL OR lease_expires_at < ?)
                ORDER BY requested_at, id
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            candidate_id = str(row["id"])
            cursor = connection.execute(
                """
                UPDATE asr_retranscription_candidates
                SET locked_by=?, lease_expires_at=?, lease_generation=lease_generation+1,
                    updated_at=?
                WHERE id=?
                  AND (locked_by IS NULL OR lease_expires_at IS NULL OR lease_expires_at < ?)
                """,
                (worker_id, expires, now, candidate_id, now),
            )
            if cursor.rowcount != 1:
                return None
            leased = connection.execute(
                "SELECT * FROM asr_retranscription_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        return self._row(leased)

    def _require_lease(self, connection: Any, candidate_id: str, worker_id: str) -> Any:
        row = connection.execute(
            "SELECT * FROM asr_retranscription_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise JobNotFound(candidate_id)
        if str(row["locked_by"] or "") != worker_id:
            raise JobConflict("重辨識候選的 worker lease 已遺失")
        return row

    def mark_submitted(
        self,
        candidate_id: str,
        worker_id: str,
        *,
        operation_name: str,
        source_audio_sha256: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.jobs.transaction() as connection:
            row = self._require_lease(connection, candidate_id, worker_id)
            connection.execute(
                """
                UPDATE asr_retranscription_candidates
                SET status='submitted', operation_name=?, source_audio_sha256=?,
                    submitted_at=COALESCE(submitted_at, ?), updated_at=?,
                    locked_by=NULL, lease_expires_at=NULL
                WHERE id=?
                """,
                (operation_name, source_audio_sha256, now, now, candidate_id),
            )
            self.jobs._event(
                connection,
                str(row["job_id"]),
                "asr_retranscription_candidate_submitted",
                worker_id,
                {
                    "candidate_id": candidate_id,
                    "chunk_index": int(row["chunk_index"]),
                    "operation_name_recorded": bool(operation_name),
                },
            )
        return self.get(candidate_id)

    def mark_processing(self, candidate_id: str, worker_id: str) -> dict[str, Any]:
        now = _iso()
        with self.jobs.transaction() as connection:
            self._require_lease(connection, candidate_id, worker_id)
            connection.execute(
                """
                UPDATE asr_retranscription_candidates
                SET status='processing', updated_at=?, locked_by=NULL, lease_expires_at=NULL
                WHERE id=?
                """,
                (now, candidate_id),
            )
        return self.get(candidate_id)

    def mark_completed(self, candidate_id: str, worker_id: str) -> dict[str, Any]:
        now = _iso()
        with self.jobs.transaction() as connection:
            row = self._require_lease(connection, candidate_id, worker_id)
            connection.execute(
                """
                UPDATE asr_retranscription_candidates
                SET status='completed', completed_at=?, updated_at=?,
                    locked_by=NULL, lease_expires_at=NULL,
                    error_kind=NULL, error_safe_message=NULL
                WHERE id=?
                """,
                (now, now, candidate_id),
            )
            self.jobs._event(
                connection,
                str(row["job_id"]),
                "asr_retranscription_candidate_completed",
                worker_id,
                {"candidate_id": candidate_id, "chunk_index": int(row["chunk_index"])},
            )
        return self.get(candidate_id)

    def mark_failed(
        self,
        candidate_id: str,
        worker_id: str,
        *,
        kind: str,
        safe_message: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.jobs.transaction() as connection:
            row = self._require_lease(connection, candidate_id, worker_id)
            connection.execute(
                """
                UPDATE asr_retranscription_candidates
                SET status='failed', failed_at=?, updated_at=?, error_kind=?,
                    error_safe_message=?, locked_by=NULL, lease_expires_at=NULL
                WHERE id=?
                """,
                (now, now, kind[:64], safe_message[-500:], candidate_id),
            )
            self.jobs._event(
                connection,
                str(row["job_id"]),
                "asr_retranscription_candidate_failed",
                worker_id,
                {
                    "candidate_id": candidate_id,
                    "chunk_index": int(row["chunk_index"]),
                    "error_kind": kind[:64],
                },
            )
        return self.get(candidate_id)

    def mark_stale(self, candidate_id: str, worker_id: str, *, reason: str) -> dict[str, Any]:
        now = _iso()
        with self.jobs.transaction() as connection:
            row = self._require_lease(connection, candidate_id, worker_id)
            connection.execute(
                """
                UPDATE asr_retranscription_candidates
                SET status='stale', updated_at=?, error_kind='source_stale',
                    error_safe_message=?, locked_by=NULL, lease_expires_at=NULL
                WHERE id=?
                """,
                (now, reason[-500:], candidate_id),
            )
            self.jobs._event(
                connection,
                str(row["job_id"]),
                "asr_retranscription_candidate_stale",
                worker_id,
                {"candidate_id": candidate_id, "reason": reason[-200:]},
            )
        return self.get(candidate_id)

    def reject(
        self,
        *,
        candidate_id: str,
        expected_job_revision: int,
        actor: str,
    ) -> dict[str, Any]:
        now = _iso()
        with self.jobs.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM asr_retranscription_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise JobNotFound(candidate_id)
            job = connection.execute(
                "SELECT revision FROM jobs WHERE id=?",
                (row["job_id"],),
            ).fetchone()
            if job is None:
                raise JobNotFound(str(row["job_id"]))
            if int(job["revision"]) != int(expected_job_revision):
                raise JobConflict("任務版本已變更；請重新載入後再決定候選")
            if str(row["status"]) != "completed":
                raise JobConflict("只有已完成的重辨識候選可以標記為不採用")
            connection.execute(
                """
                UPDATE asr_retranscription_candidates
                SET status='rejected', rejected_at=?, rejected_by=?, updated_at=?
                WHERE id=?
                """,
                (now, actor, now, candidate_id),
            )
            self.jobs._event(
                connection,
                str(row["job_id"]),
                "asr_retranscription_candidate_rejected",
                actor,
                {"candidate_id": candidate_id, "chunk_index": int(row["chunk_index"])},
            )
        return self.get(candidate_id)
