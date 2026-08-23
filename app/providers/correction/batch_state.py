"""Durable AI correction batch state (B10/B11).

Idempotency contract: for a given (job_id, source_revision, request_sha256)
there is at most ONE paid provider batch. Restart/resume re-polls the stored
provider_job_id; it never resubmits.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def request_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class AICorrectionRunStore:
    """Thin data-access layer over the ai_correction_runs table."""

    def __init__(self, connection_factory):
        # connection_factory: callable returning sqlite3.Connection w/ row_factory
        self._conn = connection_factory

    def get_existing(self, *, job_id: str, source_revision: str,
                     request_sha256: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM ai_correction_runs
                   WHERE job_id=? AND source_revision=? AND request_sha256=?""",
                (job_id, source_revision, request_sha256),
            ).fetchone()
            return dict(row) if row else None

    def record_submitted(self, *, job_id: str, source_revision: str,
                         source_sha256: str, provider: str,
                         provider_profile_id: str, model: str,
                         execution_mode: str, provider_job_id: str,
                         request_sha256: str,
                         estimated_cost_usd: float | None = None) -> int:
        now = _now()
        with self._conn() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO ai_correction_runs(
                       job_id, source_revision, source_sha256, provider,
                       provider_profile_id, model, execution_mode,
                       provider_job_id, request_sha256, status,
                       submitted_at, updated_at, estimated_cost_usd)
                   VALUES (?,?,?,?,?,?,?,?,?,'submitted',?,?,?)""",
                (job_id, source_revision, source_sha256, provider,
                 provider_profile_id, model, execution_mode,
                 provider_job_id, request_sha256, now, now,
                 estimated_cost_usd),
            )
            if cur.rowcount == 0:
                row = self.get_existing(job_id=job_id,
                                        source_revision=source_revision,
                                        request_sha256=request_sha256)
                if row is not None:
                    return int(row["id"])
                raise RuntimeError("ai_correction_runs insert failed unexpectedly")
            return int(cur.lastrowid)

    def update_status(self, run_id: int, *, status: str,
                      error_kind: str | None = None,
                      error_safe_message: str | None = None) -> None:
        sets = ["status=?", "updated_at=?"]
        params: list[Any] = [status, _now()]
        if error_kind is not None:
            sets.append("error_kind=?")
            params.append(error_kind)
        if error_safe_message is not None:
            sets.append("error_safe_message=?")
            params.append(error_safe_message)
        if status in ("completed", "failed", "cancelled", "expired"):
            sets.append("completed_at=?")
            params.append(_now())
        params.append(run_id)
        with self._conn() as c:
            c.execute(f"UPDATE ai_correction_runs SET {', '.join(sets)} WHERE id=?",
                      params)

    def record_usage(self, run_id: int, *, input_tokens: int,
                     output_tokens: int,
                     actual_cost_usd: float | None = None) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE ai_correction_runs SET input_tokens=?, output_tokens=?,
                       actual_cost_usd=?, updated_at=? WHERE id=?""",
                (input_tokens, output_tokens, actual_cost_usd, _now(), run_id))

    def pending_batches(self, providers: list[str] | None = None) -> list[dict[str, Any]]:
        """All runs still awaiting provider completion — recovery poll set."""
        q = """SELECT * FROM ai_correction_runs
               WHERE status IN ('submitted','processing')"""
        params: tuple = ()
        if providers:
            q += f" AND provider IN ({','.join('?' * len(providers))})"
            params = tuple(providers)
        with self._conn() as c:
            rows = c.execute(q + " ORDER BY submitted_at", params).fetchall()
            return [dict(r) for r in rows]

    def for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM ai_correction_runs WHERE job_id=? ORDER BY id",
                (job_id,)).fetchall()
            return [dict(r) for r in rows]


def short_batch_id(provider_job_id: str | None) -> str:
    """Safe short display form; never leaks full internal identifiers."""
    if not provider_job_id:
        return "—"
    if "/" in provider_job_id:
        provider_job_id = provider_job_id.rsplit("/", 1)[-1]
    return provider_job_id[:12]
