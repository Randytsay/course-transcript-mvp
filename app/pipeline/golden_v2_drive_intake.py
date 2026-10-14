"""Read-only Drive intake into local, immutable Golden V2 source preflight."""
from __future__ import annotations

import os
import fcntl
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable

from app.jobs.drive_api import download_drive_media
from app.pipeline.golden_v2_source_preflight import register_source_preflight

_INTAKE_LOCK = threading.Lock()


class SourceIntakeBusyError(RuntimeError):
    """Another source download currently owns the private staging space."""


def intake_drive_source(
    *, source_path: str, artifact_root: Path, database: Path, mode: str,
    estimated_stage_cents: dict[str, int], estimate_ceiling_cents: int,
    download: Callable[..., dict[str, Any]] = download_drive_media,
    register: Callable[..., dict[str, Any]] = register_source_preflight,
) -> dict[str, Any]:
    """Serialize source imports so concurrent uploads cannot exhaust staging disk."""
    if not _INTAKE_LOCK.acquire(blocking=False):
        raise SourceIntakeBusyError("another Golden V2 source intake is active")
    try:
        requested_root = Path(artifact_root).expanduser()
        ledger_path = Path(database).expanduser()
        if requested_root.is_symlink():
            raise ValueError("Golden V2 artifact root cannot be a symlink")
        if (ledger_path.is_symlink()
                or ledger_path.resolve().parent != requested_root.resolve()):
            raise ValueError("isolated Golden V2 intake root required")
        requested_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # The web importer and batch daemon share this lock across processes.
        fd = os.open(requested_root / "source-intake.lock",
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SourceIntakeBusyError("another Golden V2 source intake is active") from exc
            return _intake_drive_source_locked(
                source_path=source_path,
                artifact_root=artifact_root,
                database=database,
                mode=mode,
                estimated_stage_cents=estimated_stage_cents,
                estimate_ceiling_cents=estimate_ceiling_cents,
                download=download,
                register=register,
            )
    finally:
        _INTAKE_LOCK.release()


def _intake_drive_source_locked(
    *, source_path: str, artifact_root: Path, database: Path, mode: str,
    estimated_stage_cents: dict[str, int], estimate_ceiling_cents: int,
    download: Callable[..., dict[str, Any]],
    register: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Download one exact Drive file, bind both observations, then register.

    Only Drive GET operations and local writes occur. The new ledger row remains
    AWAITING_APPROVAL; this function never schedules an ASR or text-correction
    provider call and never mutates Drive.
    """
    requested_root = Path(artifact_root).expanduser()
    if requested_root.is_symlink():
        raise ValueError("Golden V2 artifact root cannot be a symlink")
    root = requested_root.resolve()
    ledger_path = Path(database).expanduser()
    if ledger_path.is_symlink() or ledger_path.resolve().parent != root:
        raise ValueError("Golden V2 ledger must be a regular path inside its artifact root")
    try:
        max_bytes = int(os.environ.get(
            "COURSE_TRANSCRIPT_V2_SOURCE_MAX_BYTES", str(2 * 1024**3)
        ))
    except ValueError as exc:
        raise ValueError("Golden V2 source maximum size configuration is invalid") from exc
    if max_bytes <= 0:
        raise ValueError("Golden V2 source maximum size configuration is invalid")

    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("Golden V2 artifact root escaped its configured directory")
    with tempfile.TemporaryDirectory(prefix="drive-source-", dir=root) as name:
        temporary_root = Path(name)
        os.chmod(temporary_root, 0o700)
        source = temporary_root / "selected-source"
        observation = download(source_path, source, max_bytes=max_bytes)
        result = register(
            source_media=source,
            drive_observation=observation,
            artifact_root=root,
            database=ledger_path,
            mode=mode,
            estimated_stage_cents=estimated_stage_cents,
            estimate_ceiling_cents=estimate_ceiling_cents,
        )
    return {
        **result,
        "drive_observation_authenticated": True,
        "source_path": observation["source_path"],
        "source_file_id": observation["file_id"],
        "drive_version": observation["version"],
        "source_size_bytes": observation["size_bytes"],
        "provider_calls_started": False,
        "drive_mutation_started": False,
    }
