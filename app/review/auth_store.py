"""Server-side OAuth state and reviewer session persistence.

Only random opaque browser/session tokens leave this module. SQLite stores
SHA-256 digests so a database disclosure does not directly expose reusable
session or OAuth state values.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

from .store import ReviewConflict, ReviewNotFound

OAuthAction = Literal["login", "link"]
OAuthProvider = Literal["google", "line"]


class ReviewAuthError(RuntimeError):
    pass


class ReviewAuthExpired(ReviewAuthError):
    pass


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _safe_return_to(value: str | None) -> str:
    candidate = (value or "/review").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        raise ValueError("return_to must be a same-origin absolute path")
    return candidate


class ReviewAuthStore:
    """Persistence for one-time OAuth flows and revocable reviewer sessions."""

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
                CREATE TABLE IF NOT EXISTS review_oauth_flows (
                    state_hash TEXT PRIMARY KEY,
                    provider TEXT NOT NULL CHECK(provider IN ('google', 'line')),
                    action TEXT NOT NULL CHECK(action IN ('login', 'link')),
                    user_id TEXT REFERENCES review_users(id),
                    nonce TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    return_to TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS review_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES review_users(id),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE INDEX IF NOT EXISTS review_oauth_expiry_idx
                    ON review_oauth_flows(expires_at, consumed_at);
                CREATE INDEX IF NOT EXISTS review_sessions_user_idx
                    ON review_sessions(user_id, expires_at);
                """
            )

    @staticmethod
    def _provider(provider: str) -> OAuthProvider:
        normalized = provider.strip().lower()
        if normalized not in {"google", "line"}:
            raise ValueError("provider must be google or line")
        return normalized  # type: ignore[return-value]

    def create_oauth_flow(
        self,
        *,
        provider: OAuthProvider | str,
        action: OAuthAction,
        return_to: str = "/review",
        user_id: str | None = None,
        ttl_minutes: int = 10,
    ) -> dict[str, str | None]:
        provider = self._provider(provider)
        if action not in {"login", "link"}:
            raise ValueError("action must be login or link")
        if action == "link" and not user_id:
            raise ValueError("link flow requires user_id")
        return_to = _safe_return_to(return_to)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        # token_urlsafe(48) yields ~64 RFC7636-safe characters.
        verifier = secrets.token_urlsafe(48)
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=max(1, min(ttl_minutes, 30)))
        with self.transaction() as connection:
            if user_id:
                user = connection.execute(
                    "SELECT status FROM review_users WHERE id = ?", (user_id,)
                ).fetchone()
                if user is None:
                    raise ReviewNotFound("Reviewer not found")
                if user["status"] != "active":
                    raise ReviewConflict("Reviewer account is suspended")
            connection.execute(
                """
                INSERT INTO review_oauth_flows(
                    state_hash, provider, action, user_id, nonce, code_verifier,
                    return_to, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _digest(state), provider, action, user_id, nonce, verifier,
                    return_to, _iso(now), _iso(expires),
                ),
            )
            connection.execute(
                "DELETE FROM review_oauth_flows WHERE expires_at < ?",
                (_iso(now - timedelta(days=1)),),
            )
        return {
            "state": state,
            "nonce": nonce,
            "code_verifier": verifier,
            "code_challenge": pkce_challenge(verifier),
            "return_to": return_to,
        }

    def consume_oauth_flow(
        self,
        *,
        provider: OAuthProvider | str,
        state: str,
    ) -> dict[str, Any]:
        provider = self._provider(provider)
        if not state:
            raise ReviewAuthError("OAuth state is required")
        now = datetime.now(UTC)
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM review_oauth_flows
                WHERE state_hash = ? AND provider = ?
                """,
                (_digest(state), provider),
            ).fetchone()
            if row is None:
                raise ReviewAuthError("OAuth state is invalid")
            if row["consumed_at"]:
                raise ReviewAuthError("OAuth state was already consumed")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                raise ReviewAuthExpired("OAuth state has expired")
            connection.execute(
                "UPDATE review_oauth_flows SET consumed_at = ? WHERE state_hash = ?",
                (_iso(now), row["state_hash"]),
            )
        return dict(row)

    def create_session(self, *, user_id: str, ttl_days: int = 30) -> dict[str, str]:
        token = secrets.token_urlsafe(48)
        now = datetime.now(UTC)
        expires = now + timedelta(days=max(1, min(ttl_days, 90)))
        with self.transaction() as connection:
            user = connection.execute(
                "SELECT status FROM review_users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                raise ReviewNotFound("Reviewer not found")
            if user["status"] != "active":
                raise ReviewConflict("Reviewer account is suspended")
            connection.execute(
                """
                INSERT INTO review_sessions(
                    token_hash, user_id, created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (_digest(token), user_id, _iso(now), _iso(expires), _iso(now)),
            )
            connection.execute(
                "DELETE FROM review_sessions WHERE expires_at < ?",
                (_iso(now - timedelta(days=7)),),
            )
        return {"token": token, "expires_at": _iso(expires)}

    def get_session(self, token: str, *, touch: bool = True) -> dict[str, Any]:
        if not token:
            raise ReviewAuthError("Reviewer session is required")
        now = datetime.now(UTC)
        token_hash = _digest(token)
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT s.*, u.display_name, u.avatar_url, u.role, u.status
                FROM review_sessions s
                JOIN review_users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked_at"]:
                raise ReviewAuthError("Reviewer session is invalid")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                raise ReviewAuthExpired("Reviewer session has expired")
            if row["status"] != "active":
                raise ReviewConflict("Reviewer account is suspended")
            if touch:
                connection.execute(
                    "UPDATE review_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (_iso(now), token_hash),
                )
        return dict(row)

    def revoke_session(self, token: str) -> bool:
        if not token:
            return False
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE review_sessions SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (_iso(), _digest(token)),
            )
        return cursor.rowcount == 1

    def revoke_user_sessions(self, user_id: str) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE review_sessions SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (_iso(), user_id),
            )
        return int(cursor.rowcount)
