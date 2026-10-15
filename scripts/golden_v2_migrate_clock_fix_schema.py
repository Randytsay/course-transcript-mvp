"""Explicit, auditable additive migration for an existing Golden V2 ledger.

No provider request, grant, job transition or Drive mutation is performed.
Invoke from the authorized deployment environment after a verified pause of
the paid executor. Dry-run is the default; --apply requires backup directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import hashlib
from pathlib import Path


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _summary(conn: sqlite3.Connection) -> dict:
    tables = {x[0] for x in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    required = {"v2_jobs", "v2_stages", "v2_chirp_operations"}
    if not required.issubset(tables):
        raise ValueError("not an initialized Golden V2 ledger")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(v2_chirp_operations)")}
    counts = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
              for table in sorted(required)}
    result = conn.execute(
        "SELECT COALESCE(SUM(actual_cents),0) FROM v2_chirp_operations"
    ).fetchone()[0]
    digest = hashlib.sha256()
    for table in sorted(required):
        columns = [row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")
                   if row[1] != "local_clock_fix_sha"]
        query = "SELECT " + ",".join(columns) + " FROM " + table + " ORDER BY 1,2"
        for row in conn.execute(query):
            digest.update(json.dumps(list(row), sort_keys=True,
                                     separators=(",", ":")).encode("utf-8"))
            digest.update(b"\\n")
    return {"column_present": "local_clock_fix_sha" in cols,
            "row_counts": counts, "recorded_chirp_cents": int(result),
            "data_digest": digest.hexdigest()}


def inspect_and_migrate(db_path: Path, *, backup_dir: Path | None = None,
                        apply: bool = False) -> dict:
    db_path = Path(db_path)
    if (not db_path.is_absolute() or db_path.is_symlink()
            or not db_path.is_file()):
        raise ValueError("existing non-symlink absolute ledger file required")
    with _open_readonly(db_path) as source:
        before = _summary(source)
    if before["column_present"]:
        return {"action": "ALREADY_MIGRATED", "before": before,
                "provider_calls_started": False}
    if not apply:
        return {"action": "MIGRATION_REQUIRED", "before": before,
                "provider_calls_started": False, "ledger_modified": False}
    if backup_dir is None:
        raise ValueError("explicit backup_dir required for --apply")
    backup_dir = Path(backup_dir)
    if (not backup_dir.is_absolute() or backup_dir.is_symlink()
            or not backup_dir.is_dir()
            or backup_dir.resolve() == db_path.parent.resolve()):
        raise ValueError("separate existing non-symlink backup directory required")
    backup_name = "golden-v2-pre-clock-migration-" + hashlib.sha256(
        os.fsencode(db_path.resolve())).hexdigest()[:12] + ".sqlite"
    backup_path = backup_dir / backup_name
    # Exclusive backup: never overwrite a prior accepted safety snapshot.
    fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    backup_ok = False
    try:
        with sqlite3.connect(str(db_path)) as source:
            with sqlite3.connect(str(backup_path)) as dest:
                source.backup(dest)
        with _open_readonly(backup_path) as check:
            if (check.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or _summary(check) != before):
                raise ValueError("pre-migration backup integrity mismatch")
        backup_ok = True
    finally:
        if not backup_ok:
            backup_path.unlink(missing_ok=True)
    # ALTER TABLE is transactional. The source file is never restored by
    # overwriting a live DB; operator recovery must consider later receipts.
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=15)
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("BEGIN IMMEDIATE")
        current = _summary(conn)
        if current != before:
            raise ValueError("ledger changed after backup; migration held")
        conn.execute("ALTER TABLE v2_chirp_operations ADD COLUMN local_clock_fix_sha TEXT")
        after = _summary(conn)
        if (not after["column_present"]
                or after["row_counts"] != before["row_counts"]
                or after["recorded_chirp_cents"] != before["recorded_chirp_cents"]
                or after["data_digest"] != before["data_digest"]):
            raise ValueError("migration changed ledger rows or charges")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("post-migration integrity check failed")
        conn.commit()
        return {"action": "MIGRATED", "before": before, "after": after,
                "backup_path": str(backup_path),
                "provider_calls_started": False}
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(inspect_and_migrate(
        args.database, backup_dir=args.backup_dir, apply=args.apply
    ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
