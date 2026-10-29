"""Offline-capable, root-only operator SQLite + job-artifact backup rehearsal.

Never stops/restarts workers, changes the live ledger, calls providers, reads
secrets, or publishes Drive. This tool is for an independently scoped Owner
deployment gate, NOT an automatic step of a chat request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tarfile
from tempfile import TemporaryDirectory
from typing import Any

LIVE_ROOT = Path("/opt/course-transcript-v2-chat/data/golden-v2")
BACKUPS = Path("/opt/course-transcript-v2-chat/backups")
_LABEL = re.compile(r"pr159-[0-9]{8}T[0-9]{6}Z\Z")


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def regular_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("job artifact directory unavailable or unsafe")
    found: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in job artifacts is not safe to archive")
        if path.is_file():
            found.append((path.relative_to(root.parent).as_posix(), path))
        elif not path.is_dir():
            raise ValueError("unexpected device or special file in job artifacts")
    return found


def integrity(database: Path) -> None:
    if not database.is_file() or database.is_symlink():
        raise ValueError("missing regular SQLite ledger")
    conn = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise ValueError("ledger integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("ledger foreign keys invalid")
        required = {"v2_jobs", "v2_stages", "v2_chirp_operations"}
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(tables):
            raise ValueError("not an isolated Golden V2 ledger")
    finally:
        conn.close()


def _paths(root: Path, backups: Path, label: str) -> tuple[Path, Path, Path]:
    if not _LABEL.fullmatch(label):
        raise ValueError("invalid immutable backup label")
    if root.is_symlink() or backups.is_symlink():
        raise ValueError("unsafe source or backup root symlink")
    root = root.resolve(strict=True)
    backups = backups.resolve(strict=True)
    if root == backups or root in backups.parents or backups in root.parents:
        raise ValueError("backup destination must be outside live artifact root")
    database = root / "ledger.sqlite"
    jobs = root / "jobs"
    if database.is_symlink() or jobs.is_symlink():
        raise ValueError("live ledger or jobs path is a symlink")
    return database, jobs, backups / label


def create_bundle(*, root: Path, backups: Path, label: str) -> dict[str, Any]:
    """Write a new, 0700 isolated backup; reject live changes during copy."""
    database, jobs, final = _paths(root, backups, label)
    if final.exists() or final.is_symlink():
        raise ValueError("backup name already exists; never overwrite")
    integrity(database)
    inventory = regular_files(jobs)
    total_bytes = sum(path.stat().st_size for _, path in inventory)
    free = shutil.disk_usage(backups).free
    if free < 2 * (total_bytes + database.stat().st_size) + 128 * 1024 * 1024:
        raise ValueError("not enough space for backup and isolated restore drill")
    staging = backups / (".partial-" + label)
    staging.mkdir(mode=0o700, exist_ok=False)
    try:
        os.chmod(staging, 0o700)
        backup_db = staging / "ledger.sqlite"
        source = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            source.execute("PRAGMA query_only=ON")
            initial_version = source.execute("PRAGMA data_version").fetchone()[0]
            with sqlite3.connect(backup_db) as destination:
                source.backup(destination, pages=128, sleep=0.05)
            with tarfile.open(staging / "jobs.tar.gz", "w:gz") as archive:
                for name, path in inventory:
                    archive.add(path, arcname=name, recursive=False)
            final_version = source.execute("PRAGMA data_version").fetchone()[0]
            if initial_version != final_version:
                raise ValueError("live ledger changed during artifact backup")
        finally:
            source.close()
        integrity(backup_db)
        # Compare the source file set and every byte with the archived copy
        # after completing the backup; no in-place mutation is permitted.
        current = regular_files(jobs)
        if [name for name, _ in current] != [name for name, _ in inventory]:
            raise ValueError("job files changed during artifact backup")
        files = {name: file_sha(path) for name, path in current}
        bundle = {
            "schema": "golden-v2-pr159-backup-v1",
            "label": label, "ledger_sha256": file_sha(backup_db),
            "jobs_tar_sha256": file_sha(staging / "jobs.tar.gz"),
            "file_sha256": files, "total_job_bytes": total_bytes,
            "file_count": len(files),
            "live_jobs_mutated": False, "live_ledger_mutated": False,
            "provider_calls_started": False, "drive_mutations_started": False,
        }
        (staging / "manifest.json").write_text(
            json.dumps(bundle, sort_keys=True, ensure_ascii=False))
        # Actual isolated restore + integrity and per-file SHA checks.
        verify_bundle(staging)
        staging.rename(final)
        return {"snapshot": str(final), "label": label,
                "file_count": len(files), "restore_drill_pass": True,
                "ledger_sha256": bundle["ledger_sha256"],
                "jobs_tar_sha256": bundle["jobs_tar_sha256"]}
    except BaseException:
        shutil.rmtree(staging)
        raise


def verify_bundle(bundle: Path) -> dict[str, Any]:
    """Restore to a fresh temporary directory; reject archive traversal."""
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("untrusted backup directory")
    manifest_file = bundle / "manifest.json"
    with manifest_file.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    if manifest.get("schema") != "golden-v2-pr159-backup-v1":
        raise ValueError("unsupported backup schema")
    ledger = bundle / "ledger.sqlite"
    archive_path = bundle / "jobs.tar.gz"
    if (file_sha(ledger) != manifest.get("ledger_sha256")
            or file_sha(archive_path) != manifest.get("jobs_tar_sha256")):
        raise ValueError("backup SHA mismatch")
    with TemporaryDirectory(prefix=".restore-check-", dir=bundle.parent) as temp:
        target = Path(temp)
        restored = target / "ledger.sqlite"
        shutil.copy2(ledger, restored)
        integrity(restored)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            declared = manifest.get("file_sha256")
            if (not isinstance(declared, dict)
                    or len(names) != len(set(names))
                    or set(names) != set(declared)):
                raise ValueError("backup manifest/archive membership mismatch")
            for member in members:
                name = PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute()
                        or not name.parts or name.parts[0] != "jobs"
                        or any(part in ("", ".", "..") for part in name.parts)
                        or "\\" in member.name):
                    raise ValueError("unsafe tar archive entry")
            archive.extractall(target, filter="data")
        for name, expected in declared.items():
            restored_path = target / name
            if (restored_path.is_symlink() or not restored_path.is_file()
                    or not re.fullmatch(r"[a-f0-9]{64}", expected)
                    or file_sha(restored_path) != expected):
                raise ValueError("restored job SHA mismatch")
    return {"label": manifest["label"], "restore_drill_pass": True,
            "file_count": len(manifest["file_sha256"]),
            "ledger_sha256": manifest["ledger_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("backup", "verify"))
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("protected root operator required; never bypass permissions")
    if args.mode == "backup":
        result = create_bundle(root=LIVE_ROOT, backups=BACKUPS, label=args.label)
    else:
        _, _, location = _paths(LIVE_ROOT, BACKUPS, args.label)
        result = verify_bundle(location)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
