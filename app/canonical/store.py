from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .defaults import (
    DEFAULT_TITLES,
    MANTRA_BODY,
    MANTRA_KEY,
    SCRIPTURE_KEY,
    SUPPORTED_KEYS,
)


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CanonicalTextStore:
    """Small versioned registry for canonical Buddhist publication text."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS canonical_documents (
                    document_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    active_version INTEGER,
                    active_checksum TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS canonical_document_versions (
                    document_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    source_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    PRIMARY KEY(document_key, version)
                );
                CREATE TABLE IF NOT EXISTS canonical_document_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    document_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS canonical_document_audit_created_idx
                    ON canonical_document_audit(created_at DESC);
                """
            )
            version_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_document_versions)")
            }
            if "source_json" not in version_columns:
                connection.execute(
                    "ALTER TABLE canonical_document_versions ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}'"
                )
            now = _iso()
            for key in SUPPORTED_KEYS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO canonical_documents(
                        document_key, title, active_version, active_checksum,
                        updated_at, updated_by
                    ) VALUES (?, ?, NULL, NULL, ?, 'system:bootstrap')
                    """,
                    (key, DEFAULT_TITLES[key], now),
                )
            # Preserve the exact canonical mantra already used in production as
            # version 1. Scripture is deliberately not seeded: publication must
            # wait for an operator-supplied authoritative text.
            row = connection.execute(
                "SELECT active_version FROM canonical_documents WHERE document_key=?",
                (MANTRA_KEY,),
            ).fetchone()
            if row is not None and row["active_version"] is None:
                checksum = _checksum(MANTRA_BODY)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO canonical_document_versions(
                        document_key, version, title, body_text, checksum, note, source_json,
                        created_at, created_by
                    ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        MANTRA_KEY,
                        DEFAULT_TITLES[MANTRA_KEY],
                        MANTRA_BODY,
                        checksum,
                        "Migrated from the production hard-coded mantra canonical text.",
                        json.dumps(
                            {
                                "source_type": "legacy_internal_canonical",
                                "source_id": "dacheng_mantra_seed_v1",
                            },
                            ensure_ascii=False,
                        ),
                        now,
                        "system:bootstrap",
                    ),
                )
                connection.execute(
                    """
                    UPDATE canonical_documents
                    SET active_version=1, active_checksum=?, updated_at=?, updated_by=?
                    WHERE document_key=?
                    """,
                    (checksum, now, "system:bootstrap", MANTRA_KEY),
                )
            connection.commit()

    @staticmethod
    def _validate_key(document_key: str) -> str:
        key = str(document_key or "").strip()
        if key not in SUPPORTED_KEYS:
            raise ValueError(f"unsupported canonical document key: {key}")
        return key

    def get_active(self, document_key: str) -> dict[str, Any] | None:
        key = self._validate_key(document_key)
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT d.document_key, d.title, d.active_version, d.active_checksum,
                       d.updated_at, d.updated_by,
                       v.body_text, v.note, v.source_json, v.created_at, v.created_by
                FROM canonical_documents d
                LEFT JOIN canonical_document_versions v
                  ON v.document_key=d.document_key AND v.version=d.active_version
                WHERE d.document_key=?
                """,
                (key,),
            ).fetchone()
        if row is None or row["active_version"] is None:
            return None
        result = dict(row)
        try:
            result["source"] = json.loads(str(result.pop("source_json") or "{}"))
        except (TypeError, ValueError):
            result["source"] = {}
            result.pop("source_json", None)
        return result

    def list_documents(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for key in SUPPORTED_KEYS:
            active = self.get_active(key)
            if active is None:
                result.append(
                    {
                        "document_key": key,
                        "title": DEFAULT_TITLES[key],
                        "active": False,
                        "active_version": None,
                        "active_checksum": None,
                        "body_text": "",
                    }
                )
            else:
                result.append({**active, "active": True})
        return result

    def list_versions(self, document_key: str, limit: int = 50) -> list[dict[str, Any]]:
        key = self._validate_key(document_key)
        limit = max(1, min(int(limit), 200))
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT document_key, version, title, checksum, note, source_json, created_at, created_by,
                       length(body_text) AS body_chars
                FROM canonical_document_versions
                WHERE document_key=?
                ORDER BY version DESC LIMIT ?
                """,
                (key, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["source"] = json.loads(str(item.pop("source_json") or "{}"))
            except (TypeError, ValueError):
                item["source"] = {}
                item.pop("source_json", None)
            result.append(item)
        return result

    def put_version(
        self,
        *,
        document_key: str,
        title: str,
        body_text: str,
        actor: str,
        note: str = "",
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = self._validate_key(document_key)
        title = str(title or "").strip() or DEFAULT_TITLES[key]
        body = str(body_text or "").replace("\r\n", "\n").strip()
        if not body:
            raise ValueError("canonical body_text must not be empty")
        if len(body) > 200_000:
            raise ValueError("canonical body_text must not exceed 200000 characters")
        note = str(note or "").strip()[:2000]
        source_payload = source if isinstance(source, dict) else {}
        source_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
        if len(source_json) > 20_000:
            raise ValueError("canonical source metadata must not exceed 20000 characters")
        actor = str(actor or "unknown")
        checksum = _checksum(body)
        now = _iso()
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT active_version, active_checksum FROM canonical_documents WHERE document_key=?",
                (key,),
            ).fetchone()
            if existing is None:
                raise ValueError(f"canonical document is not registered: {key}")
            if existing["active_checksum"] == checksum:
                connection.rollback()
                active = self.get_active(key)
                if active is None:
                    raise RuntimeError("canonical registry state is inconsistent")
                return {**active, "unchanged": True}
            version = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM canonical_document_versions WHERE document_key=?",
                    (key,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO canonical_document_versions(
                    document_key, version, title, body_text, checksum, note, source_json,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key, version, title, body, checksum, note, source_json, now, actor),
            )
            connection.execute(
                """
                UPDATE canonical_documents
                SET title=?, active_version=?, active_checksum=?, updated_at=?, updated_by=?
                WHERE document_key=?
                """,
                (title, version, checksum, now, actor, key),
            )
            connection.execute(
                """
                INSERT INTO canonical_document_audit(actor, action, document_key, payload_json, created_at)
                VALUES (?, 'canonical_version_activated', ?, ?, ?)
                """,
                (
                    actor,
                    key,
                    json.dumps(
                        {
                            "version": version,
                            "checksum": checksum,
                            "title": title,
                            "note": note,
                            "source": source_payload,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            connection.commit()
        active = self.get_active(key)
        if active is None:
            raise RuntimeError("canonical registry write did not produce an active version")
        return {**active, "unchanged": False}


def active_canonical(data_dir: Path, document_key: str) -> dict[str, Any] | None:
    """Read the active canonical document without creating or migrating schema.

    Correction/cleanup paths are readers. If the production DB is unavailable,
    missing, or predates the canonical tables, fail closed and return ``None``
    instead of creating directories or mutating the database as a side effect.
    Explicit admin/import flows still use ``CanonicalTextStore`` and own schema
    creation/migration.
    """
    key = str(document_key or "").strip()
    if key not in SUPPORTED_KEYS:
        raise ValueError(f"unsupported canonical document key: {key}")
    db_path = Path(data_dir) / "course-transcript.db"
    if not db_path.is_file():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        with closing(connection):
            row = connection.execute(
                """
                SELECT d.document_key, d.title, d.active_version, d.active_checksum,
                       d.updated_at, d.updated_by,
                       v.body_text, v.note, v.source_json, v.created_at, v.created_by
                FROM canonical_documents d
                LEFT JOIN canonical_document_versions v
                  ON v.document_key=d.document_key AND v.version=d.active_version
                WHERE d.document_key=?
                """,
                (key,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row["active_version"] is None:
        return None
    result = dict(row)
    try:
        result["source"] = json.loads(str(result.pop("source_json") or "{}"))
    except (TypeError, ValueError):
        result["source"] = {}
        result.pop("source_json", None)
    return result
