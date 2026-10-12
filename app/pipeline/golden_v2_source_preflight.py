"""Local-only source normalization and unapproved Golden V2 registration.

The caller supplies an observation captured alongside a read-only Drive
download. This module verifies that the observation matches local bytes; it
does not connect to or authenticate Google Drive, and never contacts providers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from typing import Any, Callable

from app.jobs.golden_v2_ledger import GoldenV2Ledger, MODES, STAGES
from app.providers.golden_v2_audio_staging import duration_seconds, file_sha256
from app.providers.golden_v2_source_lineage import bind_source_lineage


class SourcePreflightError(ValueError):
    pass


DEFAULT_ESTIMATE_CEILING_CENTS = 1000


def _wire(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _store_immutable_file(source: Path, destination: Path) -> str:
    """Stream large media into a create-once destination and verify replays."""
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise SourcePreflightError(
            f"immutable media destination is a symlink: {destination.name}"
        )
    digest = file_sha256(source)
    if destination.exists():
        if (not destination.is_file()
                or destination.stat().st_size != source.stat().st_size
                or file_sha256(destination) != digest):
            raise SourcePreflightError(
                f"existing immutable media differs: {destination.name}"
            )
        return digest

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", delete=False,
        ) as target:
            temporary_name = target.name
            with source.open("rb") as origin:
                shutil.copyfileobj(origin, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        try:
            if destination.is_symlink():
                raise SourcePreflightError(
                    f"immutable media destination is a symlink: {destination.name}"
                )
            os.link(temporary_name, destination)
        except FileExistsError:
            if (destination.is_symlink() or not destination.is_file()
                    or destination.stat().st_size != source.stat().st_size
                    or file_sha256(destination) != digest):
                raise SourcePreflightError(
                    f"concurrent immutable media conflict: {destination.name}"
                )
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return digest


def _validate_isolated_ledger_path(database: Path, artifact_root: Path) -> Path:
    database = Path(database).expanduser()
    if database.is_symlink():
        raise SourcePreflightError("V2 ledger path cannot be a symlink")
    database = database.resolve()
    if database.parent != artifact_root:
        raise SourcePreflightError(
            "V2 ledger must be in the dedicated artifact-root directory"
        )
    if database.exists():
        if not database.is_file():
            raise SourcePreflightError("V2 ledger path must be a regular file")
        if database.stat().st_size:
            try:
                with closing(sqlite3.connect(
                    f"{database.as_uri()}?mode=ro", uri=True,
                )) as db:
                    tables = {row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
            except sqlite3.Error as exc:
                raise SourcePreflightError(
                    "existing V2 ledger cannot be inspected read-only"
                ) from exc
            user_tables = {name for name in tables if not name.startswith("sqlite_")}
            if (user_tables and (
                    not {"v2_jobs", "v2_stages", "v2_chirp_operations"}.issubset(user_tables)
                    or any(not name.startswith("v2_") for name in user_tables))):
                raise SourcePreflightError(
                    "refusing to modify a non-isolated or mixed SQLite database"
                )
    return database


def _open_or_create_ledger(database: Path) -> GoldenV2Ledger:
    if database.is_file() and database.stat().st_size:
        return GoldenV2Ledger.open_existing(database)
    return GoldenV2Ledger(database)


def register_source_preflight(
    *, source_media: Path, drive_observation: dict[str, Any],
    artifact_root: Path, database: Path, mode: str,
    estimated_stage_cents: dict[str, int],
    estimate_ceiling_cents: int = DEFAULT_ESTIMATE_CEILING_CENTS,
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Normalize a previously downloaded source and register it for approval.

    ``drive_observation`` must contain provider, file_id, version, size_bytes,
    and downloaded_sha256. Matching local bytes proves consistency with those
    supplied fields, not that Google Drive issued them. Estimates are caller
    input only; they are neither provider quotes nor cost authorization.
    """
    source = Path(source_media).resolve(strict=True)
    if not source.is_file():
        raise SourcePreflightError("source media file required")
    if mode not in MODES:
        raise SourcePreflightError("unsupported Golden V2 mode")
    if (not isinstance(estimated_stage_cents, dict)
            or set(estimated_stage_cents) != set(STAGES)
            or any(type(value) is not int or value < 0
                   for value in estimated_stage_cents.values())):
        raise SourcePreflightError("complete non-negative per-stage estimates required")
    if (type(estimate_ceiling_cents) is not int or estimate_ceiling_cents <= 0
            or sum(estimated_stage_cents.values()) > estimate_ceiling_cents):
        raise SourcePreflightError("estimate exceeds configured preflight ceiling")
    if not isinstance(drive_observation, dict):
        raise SourcePreflightError("caller-supplied Drive observation required")

    root = Path(artifact_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    database_path = _validate_isolated_ledger_path(Path(database), root)
    jobs_root = root / "jobs"
    if jobs_root.is_symlink():
        raise SourcePreflightError("V2 jobs directory cannot be a symlink")
    if jobs_root.exists() and not jobs_root.is_dir():
        raise SourcePreflightError("V2 jobs path must be a directory")
    jobs_root.mkdir(exist_ok=True)
    if jobs_root.is_symlink() or jobs_root.resolve() != jobs_root:
        raise SourcePreflightError("V2 jobs directory escaped the artifact root")
    with tempfile.TemporaryDirectory(prefix="golden-v2-source-", dir=root) as temp_name:
        temporary_root = Path(temp_name)
        source_snapshot = temporary_root / "source.original"
        snapshot_sha = _store_immutable_file(source, source_snapshot)
        if (type(drive_observation.get("size_bytes")) is not int
                or source_snapshot.stat().st_size != drive_observation["size_bytes"]
                or drive_observation.get("downloaded_sha256") != snapshot_sha):
            raise SourcePreflightError(
                "local source snapshot disagrees with supplied Drive download observation"
            )
        normalized = temporary_root / "normalized.flac"
        try:
            run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-i", str(source_snapshot), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                 "-c:a", "flac", "-y", "-f", "flac", str(normalized)],
                check=True, capture_output=True, text=True, timeout=7200,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SourcePreflightError("local ffmpeg normalization failed") from exc
        if not normalized.is_file() or normalized.stat().st_size <= 0:
            raise SourcePreflightError("ffmpeg did not produce a non-empty FLAC")

        lineage_path = temporary_root / "source-lineage.json"
        try:
            lineage = bind_source_lineage(
                original_media=source_snapshot,
                normalized_audio=normalized,
                drive_observation=drive_observation,
                expected_normalized_sha256=file_sha256(normalized),
                output_path=lineage_path,
                duration_probe=lambda path: duration_seconds(path, run=run),
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise SourcePreflightError(f"source lineage validation failed: {exc}") from exc

        registration = {
            "schema": "golden-v2-source-preflight-v1",
            "source_sha256": lineage["manifest"]["normalized_sha256"],
            "original_sha256": lineage["manifest"]["original_sha256"],
            "source_lineage_sha256": lineage["manifest_sha256"],
            "drive_observation": lineage["manifest"]["drive_observation"],
            "mode": mode,
            "estimated_stage_cents": {
                name: estimated_stage_cents[name] for name in STAGES
            },
            "estimates_are_provider_quotes": False,
            "estimates_are_authorization": False,
            "estimate_ceiling_cents": estimate_ceiling_cents,
            "owner_approval_required": True,
            "provider_calls_started": False,
            "drive_mutation_started": False,
        }
        registration_bytes = _wire(registration)
        registration_sha = hashlib.sha256(registration_bytes).hexdigest()
        job_id = GoldenV2Ledger.preflight_job_id(registration_sha)
        job_dir = jobs_root / job_id
        if job_dir.is_symlink():
            raise SourcePreflightError("V2 job directory cannot be a symlink")
        job_dir.mkdir(exist_ok=True)
        if (job_dir.is_symlink() or job_dir.resolve().parent != jobs_root
                or job_dir.resolve() != job_dir):
            raise SourcePreflightError("V2 job directory escaped the artifact root")

        _store_immutable_file(source_snapshot, job_dir / "source.original")
        _store_immutable_file(normalized, job_dir / "normalized.flac")
        _store_immutable_file(lineage_path, job_dir / "source-lineage.json")
        from app.providers.golden_v2_immutable import store_immutable_bytes
        store_immutable_bytes(job_dir / "source-preflight.json", registration_bytes)

    try:
        ledger = _open_or_create_ledger(database_path)
    except (OSError, sqlite3.Error) as exc:
        raise SourcePreflightError("isolated V2 ledger initialization failed") from exc
    registered_id = ledger.create_from_preflight(
        source_sha=registration["source_sha256"], mode=mode,
        estimated_stage_cents=estimated_stage_cents,
        registration_sha256=registration_sha,
    )
    if registered_id != job_id:
        raise SourcePreflightError("ledger registration identity changed")
    state = ledger.status(job_id)
    return {
        "job_id": job_id,
        "status": state["status"],
        "revision": state["revision"],
        "job_dir": str(job_dir),
        "source_sha256": registration["source_sha256"],
        "source_lineage_sha256": registration["source_lineage_sha256"],
        "registration_sha256": registration_sha,
        "estimated_cents": sum(estimated_stage_cents.values()),
        "approval_required": state["status"] == "AWAITING_APPROVAL",
        "owner_approval_recorded": state["status"] != "AWAITING_APPROVAL",
        "drive_observation_authenticated": False,
        "source_bytes_uploaded": False,
        "provider_calls_started": False,
        "drive_mutation_started": False,
    }
