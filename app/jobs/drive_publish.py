"""Explicit, rate-limit-safe publication of reviewed artifacts to Google Drive.

This module is deliberately not wired into the transcription pipeline or web
API.  Publishing remains an operator-approved action after QA.  It uses the
existing rclone credential mount without ever reading or writing that secret.
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
from pathlib import Path
from typing import Any

from .exports import normalize_output_formats


RETRY_DELAYS_SECONDS = (30.0, 60.0, 120.0)


class DrivePublishError(RuntimeError):
    """An explicit Drive publication could not be verified."""


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
    ),
    "txt": (
        PublishArtifact("txt", "transcript-corrected.txt", ".txt"),
        PublishArtifact("txt", "transcript_corrected.txt", ".txt"),
        PublishArtifact("txt", "transcript-raw.txt", ".txt"),
    ),
    "csv": (PublishArtifact("csv", "transcript-segments.csv", ".csv"),),
    "vtt": (
        PublishArtifact("vtt", "subtitles-corrected.vtt", ".vtt"),
        PublishArtifact("vtt", "subtitles.vtt", ".vtt"),
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _safe_destination(destination: str) -> str:
    if not destination.startswith("gdrive:"):
        raise DrivePublishError("Drive publication destination must use the gdrive: remote")
    relative = destination.removeprefix("gdrive:").strip("/")
    if not relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise DrivePublishError("Drive publication destination is not a safe folder path")
    return f"gdrive:{relative}"


def _artifact_for(job_dir: Path, output_format: str) -> PublishArtifact:
    for candidate in _ARTIFACTS[output_format]:
        path = job_dir / candidate.local_name
        if path.is_file() and path.stat().st_size > 0:
            return candidate
    names = ", ".join(item.local_name for item in _ARTIFACTS[output_format])
    raise DrivePublishError(f"Missing verified local artifact for {output_format}: {names}")


def _is_rate_limited(result: subprocess.CompletedProcess[str]) -> bool:
    message = f"{result.stdout}\n{result.stderr}".lower()
    return "ratelimitexceeded" in message or "user ratelimit" in message or "quota exceeded" in message


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _remote_size(
    remote_path: str,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> int:
    result = runner(["rclone", "size", "--json", "--tpslimit", "1", "--tpslimit-burst", "1", remote_path])
    if result.returncode != 0:
        if _is_rate_limited(result):
            raise DriveRateLimited("Drive rate limited the publication read-back")
        raise DrivePublishError("Drive publication read-back failed")
    try:
        payload = json.loads(result.stdout)
        return int(payload["bytes"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DrivePublishError("Drive publication read-back returned invalid metadata") from exc


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
) -> dict[str, Any]:
    """Publish selected artifacts one at a time with bounded quota backoff.

    A completed state is resumable: it is not re-uploaded on a later invocation.
    The caller must explicitly set ``authorized=True`` for every invocation.
    """
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
    state: dict[str, Any]
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("destination") != destination:
            raise DrivePublishError("Existing publication state belongs to another Drive folder")
    else:
        state = {"version": 1, "destination": destination, "source_name": source_name, "files": {}}
    state["authorized_at"] = _utcnow()
    state["status"] = "in_progress"
    _write_state(state_path, state)

    for output_format in formats:
        artifact = _artifact_for(job_dir, output_format)
        local_path = job_dir / artifact.local_name
        remote_path = f"{destination}/{base_name}{artifact.suffix}"
        files: dict[str, Any] = state["files"]
        current = files.get(output_format, {})
        if current.get("status") == "completed":
            continue
        record = {
            "format": output_format,
            "local_name": artifact.local_name,
            "remote_path": remote_path,
            "bytes": local_path.stat().st_size,
            "sha256": _sha256(local_path),
            "status": "pending",
            "attempts": int(current.get("attempts", 0)),
        }
        files[output_format] = record
        _write_state(state_path, state)

        for retry_index in range(len(RETRY_DELAYS_SECONDS) + 1):
            record["attempts"] += 1
            record["status"] = "uploading"
            record["updated_at"] = _utcnow()
            _write_state(state_path, state)
            command = [
                "rclone", "copyto", "--checksum", "--retries", "1", "--low-level-retries", "1",
                "--tpslimit", "1", "--tpslimit-burst", "1", str(local_path), remote_path,
            ]
            result = runner(command)
            if result.returncode == 0:
                try:
                    remote_bytes = _remote_size(remote_path, runner=runner)
                except DriveRateLimited:
                    result = subprocess.CompletedProcess(command, 1, "", "rateLimitExceeded during read-back")
                else:
                    if remote_bytes != record["bytes"]:
                        raise DrivePublishError("Drive publication read-back size does not match local artifact")
                    record.update({"status": "completed", "verified_at": _utcnow(), "remote_bytes": remote_bytes})
                    _write_state(state_path, state)
                    break
            if not _is_rate_limited(result):
                record.update({"status": "failed", "error": "rclone upload failed; no sensitive response was persisted"})
                state["status"] = "failed"
                _write_state(state_path, state)
                raise DrivePublishError(f"Drive publication failed for {output_format}")
            if retry_index == len(RETRY_DELAYS_SECONDS):
                record.update({"status": "rate_limited", "error": "Drive quota retry window exhausted"})
                state["status"] = "rate_limited"
                _write_state(state_path, state)
                raise DriveRateLimited(f"Drive publication rate limited for {output_format}; resume later")
            delay = RETRY_DELAYS_SECONDS[retry_index] + round(jitter() * 5, 3)
            record.update({"status": "rate_limited", "next_retry_after_seconds": delay})
            state["status"] = "rate_limited"
            _write_state(state_path, state)
            sleeper(delay)
            state["status"] = "in_progress"
            _write_state(state_path, state)
        else:  # pragma: no cover - the retry loop exits via success or exception
            raise DrivePublishError("Drive publication retry loop ended unexpectedly")

    state["status"] = "completed"
    state["completed_at"] = _utcnow()
    _write_state(state_path, state)
    return state


def main() -> int:
    """Operator-only entrypoint; deliberately opt-in rather than automatic."""
    if os.environ.get("PUBLISH_TO_DRIVE") != "1":
        raise SystemExit("Refusing Drive publication: set PUBLISH_TO_DRIVE=1 after explicit approval")
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    job_name = os.environ.get("JOB_NAME", "")
    destination = os.environ.get("DRIVE_OUTPUT_DIRECTORY", "")
    source_name = os.environ.get("SOURCE_NAME", "")
    formats = json.loads(os.environ.get("OUTPUT_FORMATS_JSON", '["srt", "txt", "csv"]'))
    state = publish_outputs(
        data_dir / "jobs" / job_name,
        source_name=source_name,
        destination=destination,
        output_formats=formats,
        authorized=True,
    )
    print(f"DRIVE_PUBLISH=PASS files={len(state['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
