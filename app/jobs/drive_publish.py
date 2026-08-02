"""Rate-limit-safe, non-overwriting publication of derived Drive artifacts.

A generated subtitle never overwrites an existing same-name file. The publisher
first uploads to a job-specific pending name, verifies it, renames any existing
final file to a timestamped backup, promotes the pending file, and verifies the
new final object. Every phase is persisted so a restart resumes without
re-running Chirp or Gemini.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .exports import normalize_output_formats

RETRY_DELAYS_SECONDS = (30.0, 60.0, 120.0)
MINIMUM_DRIVE_REQUEST_INTERVAL_SECONDS = 1.0


class DrivePublishError(RuntimeError):
    """A Drive publication could not be verified."""


class DriveRateLimited(DrivePublishError):
    """Google Drive rejected a publication request due to quota pressure."""


@dataclass(frozen=True)
class PublishArtifact:
    format: str
    local_name: str
    suffix: str


_ARTIFACTS: dict[str, tuple[PublishArtifact, ...]] = {
    "srt": (
        PublishArtifact("srt", "subtitles-corrected.srt", ".srt"),
        PublishArtifact("srt", "subtitles.srt", ".srt"),
        PublishArtifact("srt", "transcript.srt", ".srt"),
    ),
    "txt": (
        PublishArtifact("txt", "transcript-corrected.txt", ".txt"),
        PublishArtifact("txt", "transcript_corrected.txt", ".txt"),
        PublishArtifact("txt", "transcript-raw.txt", ".txt"),
        PublishArtifact("txt", "transcript_raw.txt", ".txt"),
    ),
    "csv": (
        PublishArtifact("csv", "transcript-segments.csv", ".csv"),
        PublishArtifact("csv", "transcript.csv", ".csv"),
    ),
    "vtt": (
        PublishArtifact("vtt", "subtitles-corrected.vtt", ".vtt"),
        PublishArtifact("vtt", "subtitles.vtt", ".vtt"),
        PublishArtifact("vtt", "transcript.vtt", ".vtt"),
    ),
    "ass": (
        PublishArtifact("ass", "subtitles-corrected.ass", ".ass"),
        PublishArtifact("ass", "subtitles.ass", ".ass"),
    ),
    "docx": (PublishArtifact("docx", "transcript.docx", ".docx"),),
    "pdf": (PublishArtifact("pdf", "transcript.pdf", ".pdf"),),
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _backup_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_destination(destination: str) -> str:
    if not destination.startswith("gdrive:"):
        raise DrivePublishError("Drive publication destination must use the gdrive: remote")
    relative = destination.removeprefix("gdrive:").strip("/")
    if not relative:
        return "gdrive:"
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise DrivePublishError("Drive publication destination is not a safe folder path")
    return f"gdrive:{relative}"


def source_parent_destination(source_path: str) -> str:
    """Return the exact Drive folder containing a validated ``gdrive:`` file."""
    if not source_path.startswith("gdrive:"):
        raise DrivePublishError("Drive source path must use the gdrive: remote")
    relative = source_path.removeprefix("gdrive:").strip("/")
    source = PurePosixPath(relative)
    if not relative or source.name in {"", ".", ".."}:
        raise DrivePublishError("Drive source path must name a file")
    if any(part in {"", ".", ".."} for part in source.parts):
        raise DrivePublishError("Drive source path is not safe")
    parent = source.parent.as_posix()
    return _safe_destination("gdrive:" if parent == "." else f"gdrive:{parent}")


def _join_remote(destination: str, filename: str) -> str:
    if "/" in filename or filename in {"", ".", ".."}:
        raise DrivePublishError("Drive output filename is unsafe")
    return f"{destination}/{filename}" if destination != "gdrive:" else f"gdrive:{filename}"


def _artifact_for(job_dir: Path, output_format: str) -> PublishArtifact:
    for candidate in _ARTIFACTS[output_format]:
        path = job_dir / candidate.local_name
        if path.is_file() and path.stat().st_size > 0:
            return candidate
    names = ", ".join(item.local_name for item in _ARTIFACTS[output_format])
    raise DrivePublishError(f"Missing verified local artifact for {output_format}: {names}")


def _is_rate_limited(result: subprocess.CompletedProcess[str]) -> bool:
    message = f"{result.stdout}\n{result.stderr}".lower()
    return any(
        marker in message
        for marker in ("ratelimitexceeded", "user ratelimit", "quota exceeded", "too many requests")
    )


def _is_not_found(result: subprocess.CompletedProcess[str]) -> bool:
    message = f"{result.stdout}\n{result.stderr}".lower()
    return any(
        marker in message
        for marker in (
            "object not found",
            "directory not found",
            "file not found",
            "couldn't find",
            "not found",
        )
    )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _remote_size(
    remote_path: str,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
    missing_ok: bool = False,
) -> int | None:
    result = runner(
        [
            "rclone",
            "size",
            "--json",
            "--tpslimit",
            "1",
            "--tpslimit-burst",
            "1",
            remote_path,
        ]
    )
    if result.returncode != 0:
        if _is_rate_limited(result):
            raise DriveRateLimited("Drive rate limited the publication read-back")
        if missing_ok and _is_not_found(result):
            return None
        raise DrivePublishError("Drive publication read-back failed")
    try:
        payload = json.loads(result.stdout)
        count = int(payload.get("count", 0))
        if count <= 0:
            return None if missing_ok else 0
        return int(payload["bytes"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DrivePublishError("Drive publication read-back returned invalid metadata") from exc


def _retry_rate_limited(
    action: Callable[[], subprocess.CompletedProcess[str]],
    *,
    sleeper: Callable[[float], None],
    jitter: Callable[[], float],
    description: str,
) -> subprocess.CompletedProcess[str]:
    last: subprocess.CompletedProcess[str] | None = None
    for retry_index in range(len(RETRY_DELAYS_SECONDS) + 1):
        result = action()
        last = result
        if result.returncode == 0:
            return result
        if not _is_rate_limited(result):
            return result
        if retry_index == len(RETRY_DELAYS_SECONDS):
            break
        sleeper(RETRY_DELAYS_SECONDS[retry_index] + round(jitter() * 5, 3))
    raise DriveRateLimited(f"Drive quota retry window exhausted during {description}")


def _unique_backup_path(
    final_path: str,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> str:
    prefix, suffix = os.path.splitext(final_path)
    stamp = _backup_stamp()
    for sequence in range(0, 100):
        candidate = f"{prefix}.backup-{stamp}{f'-{sequence:02d}' if sequence else ''}{suffix}"
        if _remote_size(candidate, runner=runner, missing_ok=True) is None:
            return candidate
    raise DrivePublishError("Unable to allocate a unique Drive backup filename")


def publish_outputs(
    job_dir: Path,
    *,
    source_name: str,
    destination: str,
    output_formats: Iterable[object] | None,
    authorized: bool,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Publish selected artifacts with a durable backup-before-promote transaction."""
    if not authorized:
        raise DrivePublishError("Drive publication requires explicit operator authorization")
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise DrivePublishError("Drive publication job directory does not exist")
    destination = _safe_destination(destination)
    formats = normalize_output_formats(output_formats)
    base_name = Path(source_name).stem
    if not base_name or base_name in {".", ".."}:
        raise DrivePublishError("Drive publication source name is invalid")

    state_path = job_dir / "drive-publish-state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("destination") != destination:
            raise DrivePublishError("Existing publication state belongs to another Drive folder")
    else:
        state = {
            "version": 2,
            "destination": destination,
            "source_name": source_name,
            "job_id": job_dir.name,
            "files": {},
        }
    state["authorized_at"] = _utcnow()
    state["status"] = "in_progress"
    _write_state(state_path, state)

    last_request_at: float | None = None

    def limited_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal last_request_at
        if last_request_at is not None:
            delay = MINIMUM_DRIVE_REQUEST_INTERVAL_SECONDS - (clock() - last_request_at)
            if delay > 0:
                sleeper(delay)
        result = runner(command)
        last_request_at = clock()
        return result

    for output_format in formats:
        artifact = _artifact_for(job_dir, output_format)
        local_path = job_dir / artifact.local_name
        final_name = f"{base_name}{artifact.suffix}"
        final_path = _join_remote(destination, final_name)
        pending_name = f".{base_name}.{job_dir.name[:16]}.pending{artifact.suffix}"
        pending_path = _join_remote(destination, pending_name)
        local_bytes = local_path.stat().st_size
        files: dict[str, Any] = state["files"]
        current = files.get(output_format, {})
        record = {
            "format": output_format,
            "local_name": artifact.local_name,
            "final_remote_path": final_path,
            "pending_remote_path": pending_path,
            "bytes": local_bytes,
            "sha256": _sha256(local_path),
            "status": current.get("status", "pending"),
            "phase": current.get("phase", "pending_upload"),
            "attempts": int(current.get("attempts", 0)),
            "backup_remote_path": current.get("backup_remote_path"),
            "backup_bytes": current.get("backup_bytes"),
        }
        files[output_format] = record
        _write_state(state_path, state)

        if record["status"] == "completed" or record["phase"] == "promoting":
            final_bytes = _remote_size(final_path, runner=limited_runner, missing_ok=True)
            if final_bytes == local_bytes:
                record.update(
                    status="completed",
                    phase="completed",
                    remote_bytes=final_bytes,
                    verified_at=_utcnow(),
                )
                _write_state(state_path, state)
                continue
            if record["status"] == "completed":
                record.update(status="pending", phase="pending_upload")

        pending_bytes = _remote_size(pending_path, runner=limited_runner, missing_ok=True)
        if pending_bytes != local_bytes:
            record.update(status="uploading", phase="pending_upload", updated_at=_utcnow())
            record["attempts"] += 1
            _write_state(state_path, state)
            command = [
                "rclone",
                "copyto",
                "--checksum",
                "--retries",
                "1",
                "--low-level-retries",
                "1",
                "--tpslimit",
                "1",
                "--tpslimit-burst",
                "1",
                str(local_path),
                pending_path,
            ]
            result = _retry_rate_limited(
                lambda: limited_runner(command),
                sleeper=sleeper,
                jitter=jitter,
                description=f"upload {output_format}",
            )
            if result.returncode != 0:
                record.update(status="failed", error="rclone pending upload failed")
                state["status"] = "failed"
                _write_state(state_path, state)
                raise DrivePublishError(f"Drive pending upload failed for {output_format}")
            pending_bytes = _remote_size(pending_path, runner=limited_runner)
        if pending_bytes != local_bytes:
            raise DrivePublishError("Drive pending upload size does not match local artifact")
        if record.get("phase") not in {"backup_completed", "promoting", "completed"}:
            record.update(status="in_progress", phase="pending_verified", pending_bytes=pending_bytes)
            _write_state(state_path, state)

        if record.get("phase") not in {"backup_completed", "promoting", "completed"}:
            final_bytes = _remote_size(final_path, runner=limited_runner, missing_ok=True)
            if final_bytes is not None:
                backup_path = record.get("backup_remote_path") or _unique_backup_path(
                    final_path,
                    runner=limited_runner,
                )
                move = [
                    "rclone",
                    "moveto",
                    "--tpslimit",
                    "1",
                    "--tpslimit-burst",
                    "1",
                    final_path,
                    backup_path,
                ]
                result = _retry_rate_limited(
                    lambda: limited_runner(move),
                    sleeper=sleeper,
                    jitter=jitter,
                    description=f"backup existing {output_format}",
                )
                if result.returncode != 0:
                    raise DrivePublishError(f"Unable to back up existing {output_format}")
                backup_bytes = _remote_size(backup_path, runner=limited_runner)
                if backup_bytes != final_bytes:
                    raise DrivePublishError("Drive backup size verification failed")
                record.update(
                    backup_remote_path=backup_path,
                    backup_bytes=backup_bytes,
                    backup_created_at=_utcnow(),
                )
            record["phase"] = "backup_completed"
            _write_state(state_path, state)

        record["phase"] = "promoting"
        _write_state(state_path, state)
        promote = [
            "rclone",
            "moveto",
            "--tpslimit",
            "1",
            "--tpslimit-burst",
            "1",
            pending_path,
            final_path,
        ]
        result = _retry_rate_limited(
            lambda: limited_runner(promote),
            sleeper=sleeper,
            jitter=jitter,
            description=f"promote {output_format}",
        )
        if result.returncode != 0:
            raise DrivePublishError(f"Unable to promote new {output_format}")
        final_bytes = _remote_size(final_path, runner=limited_runner)
        if final_bytes != local_bytes:
            raise DrivePublishError("Drive final file size does not match local artifact")
        record.update(
            status="completed",
            phase="completed",
            remote_bytes=final_bytes,
            verified_at=_utcnow(),
            error=None,
        )
        _write_state(state_path, state)

    state["status"] = "completed"
    state["completed_at"] = _utcnow()
    state["backup_count"] = sum(
        1 for item in state["files"].values() if item.get("backup_remote_path")
    )
    _write_state(state_path, state)
    return state


def main() -> int:
    """Explicit operator entrypoint for one job."""
    if os.environ.get("PUBLISH_TO_DRIVE") != "1":
        raise SystemExit("Refusing Drive publication: set PUBLISH_TO_DRIVE=1 after explicit approval")
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    job_name = os.environ.get("JOB_NAME", "")
    destination = os.environ.get("DRIVE_OUTPUT_DIRECTORY", "")
    source_name = os.environ.get("SOURCE_NAME", "")
    source_path = os.environ.get("SOURCE_PATH", "")
    if not destination:
        destination = source_parent_destination(source_path)
    formats = json.loads(os.environ.get("OUTPUT_FORMATS_JSON", '["srt", "txt"]'))
    state = publish_outputs(
        data_dir / "jobs" / job_name,
        source_name=source_name,
        destination=destination,
        output_formats=formats,
        authorized=True,
    )
    print(
        f"DRIVE_PUBLISH=PASS files={len(state['files'])} backups={state['backup_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
