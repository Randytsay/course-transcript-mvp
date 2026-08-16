"""Non-paid, sequential Drive media preflight worker.

The worker copies one approved source at a time into a controlled temporary
directory, probes it locally, records a checksum and estimated cost, then
removes the temporary copy. It never calls Speech-to-Text, Vertex AI, or GCS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from app.jobs.costs import CostConfig, estimate_job_cost
from app.jobs.rclone_auth import rclone_environment
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY
from app.jobs.store import JobConflict, JobStore


class PreflightError(RuntimeError):
    pass


def _run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=rclone_environment() if command and command[0] == "rclone" else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError("本機媒體檢查命令無法在期限內完成") from exc
    if result.returncode != 0:
        raise PreflightError("本機媒體檢查命令失敗")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probe(path: Path) -> dict[str, Any]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ],
        timeout_seconds=120,
    )
    try:
        payload = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PreflightError("FFprobe 未回傳有效音訊長度") from exc
    if duration <= 0:
        raise PreflightError("來源影音長度必須大於零")
    audio_stream = next(
        (
            stream
            for stream in payload.get("streams", [])
            if stream.get("codec_type") == "audio"
        ),
        None,
    )
    if audio_stream is None:
        raise PreflightError("來源檔沒有可辨識的音軌")
    return {
        "duration_seconds": duration,
        "media_format": payload.get("format", {}).get("format_name"),
        "audio_codec": audio_stream.get("codec_name"),
    }


def _check_disk(work_root: Path, source_size_bytes: int) -> None:
    minimum_free_gb = float(
        os.environ.get("COURSE_TRANSCRIPT_MINIMUM_FREE_SPACE_GB", "3")
    )
    free_bytes = shutil.disk_usage(work_root).free
    required = int(minimum_free_gb * 1024**3) + int(source_size_bytes * 1.25)
    if free_bytes < required:
        raise PreflightError(
            f"VPS 可用空間不足；需保留至少 {minimum_free_gb:g} GB 安全空間"
        )


def run_preflight(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    leased = store.acquire_lease(record["id"], worker_id, lease_seconds=7200)
    if leased["status"] != "preflight":
        store.release_lease(record["id"], worker_id)
        raise JobConflict("任務不在 preflight 狀態")

    work_root = data_dir / "tmp" / "preflight"
    work_root.mkdir(parents=True, exist_ok=True)
    _check_disk(work_root, int(leased["source_size_bytes"]))
    suffix = Path(leased["source_name"]).suffix.lower()
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"{leased['id']}-", dir=work_root
        ) as temporary:
            local_source = Path(temporary) / f"source{suffix}"
            _run(
                ["rclone", "copyto", "--immutable", leased["source_path"], str(local_source)],
                timeout_seconds=int(
                    os.environ.get("COURSE_TRANSCRIPT_RCLONE_COPY_TIMEOUT_SECONDS", "7200")
                ),
            )
            if not local_source.is_file() or local_source.stat().st_size <= 0:
                raise PreflightError("rclone 未建立有效的本機來源檔")
            checksum = _sha256(local_source)
            probe = _probe(local_source)
            strategy = record.get("processing_strategy") or DEFAULT_PROCESSING_STRATEGY
            estimate = estimate_job_cost(
                probe["duration_seconds"],
                CostConfig.from_env().for_processing_strategy(strategy),
            )
        return store.record_preflight_result(
            job_id=leased["id"],
            duration_seconds=probe["duration_seconds"],
            source_checksum=checksum,
            media_format=probe["media_format"],
            audio_codec=probe["audio_codec"],
            estimated_cost_usd=estimate.estimated_total_usd,
            pricing_version=estimate.pricing_version,
            worker_id=worker_id,
        )
    except Exception as exc:
        try:
            store.fail_job(
                job_id=leased["id"],
                stage="preflight",
                error=str(exc),
                worker_id=worker_id,
            )
        except JobConflict:
            pass
        raise


def run_once(store: JobStore, *, data_dir: Path, worker_id: str) -> bool:
    record = store.next_job_for_status("preflight")
    if record is None:
        return False
    try:
        run_preflight(store, record, data_dir=data_dir, worker_id=worker_id)
    except Exception:
        return True
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    store = JobStore(data_dir / "course-transcript.db")
    worker_id = os.environ.get("COURSE_TRANSCRIPT_WORKER_ID", "preflight-worker")

    if args.once:
        run_once(store, data_dir=data_dir, worker_id=worker_id)
        return 0
    while True:
        worked = run_once(store, data_dir=data_dir, worker_id=worker_id)
        if not worked:
            time.sleep(max(1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
