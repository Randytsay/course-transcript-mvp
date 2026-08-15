"""Persist user-selected text-correction policy without changing job semantics.

This module intentionally does not call any model provider. It stores the user's
requested routing policy separately from the core jobs table so production can
keep Gemini 3.7 Flash as the safe default until MiniMax M3 credentials, quota
behaviour, and quality are validated in the target runtime.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

GEMINI_FIRST = "GEMINI_FIRST"
M3_FIRST = "M3_FIRST"
VALID_CORRECTION_POLICIES = frozenset({GEMINI_FIRST, M3_FIRST})
DEFAULT_CORRECTION_POLICY = GEMINI_FIRST


def normalize_correction_policy(value: object) -> str:
    candidate = str(value or DEFAULT_CORRECTION_POLICY).strip().upper()
    if candidate not in VALID_CORRECTION_POLICIES:
        raise ValueError(f"Unsupported correction policy: {value}")
    return candidate


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_table(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS job_correction_policies (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            requested_policy TEXT NOT NULL
                CHECK(requested_policy IN ('GEMINI_FIRST', 'M3_FIRST')),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _upsert(
    connection: Any,
    *,
    job_id: str,
    policy: str,
    actor: str,
    now: str,
) -> None:
    exists = connection.execute(
        "SELECT id FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if exists is None:
        raise LookupError("Job not found")
    connection.execute(
        """
        INSERT INTO job_correction_policies(
            job_id, requested_policy, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            requested_policy = excluded.requested_policy,
            updated_at = excluded.updated_at
        """,
        (job_id, policy, actor, now, now),
    )


def set_job_correction_policy(
    store: Any,
    *,
    job_id: str,
    policy: str,
    actor: str,
) -> str:
    normalized = normalize_correction_policy(policy)
    now = _iso_now()
    with store.transaction() as connection:
        _ensure_table(connection)
        _upsert(
            connection,
            job_id=job_id,
            policy=normalized,
            actor=actor,
            now=now,
        )
    return normalized


def set_batch_correction_policy(
    store: Any,
    *,
    job_ids: list[str],
    policy: str,
    actor: str,
) -> str:
    normalized = normalize_correction_policy(policy)
    now = _iso_now()
    with store.transaction() as connection:
        _ensure_table(connection)
        for job_id in job_ids:
            _upsert(
                connection,
                job_id=job_id,
                policy=normalized,
                actor=actor,
                now=now,
            )
    return normalized


def get_job_correction_policy(store: Any, job_id: str) -> str:
    with store.transaction() as connection:
        _ensure_table(connection)
        row = connection.execute(
            "SELECT requested_policy FROM job_correction_policies WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return DEFAULT_CORRECTION_POLICY
    return normalize_correction_policy(row["requested_policy"])
