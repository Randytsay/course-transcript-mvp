"""Cancellation-aware non-paid Drive media preflight worker."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from app.jobs.costs import CostConfig, estimate_job_cost
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY
from app.jobs.preflight import PreflightError, _check_disk, _probe, _sha256
from app.jobs.store import JobConflict, JobStore
from app.operations.runtime_heartbeat import write_service_heartbeat


class PreflightCancelled(RuntimeError):
    pass


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=5)


def _copy_source(
    store: JobStore,
    record: dict[str, Any],
    local_source: Path,
    *,
    timeout_seconds: int,
) -> None:
    process = subprocess.Popen(
        [
            "rclone",
            "copyto",
            "--immutable",
            record["source_path"],
            str(local_source),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    started = time.monotonic()
    while process.poll() is None:
        if time.monotonic() - started > timeout_seconds:
            _terminate(process)
            raise PreflightError("本機媒體檢查命令無法在期限內完成")
        status = str(store.get_job(record["id"])["status"])
        if status in {"cancelled", "cancelling"}:
            _terminate(process)
            raise PreflightCancelled("任務已由使用者取消")
        time.sleep(1)
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise PreflightError(
            f"本機媒體檢查命令失敗：{stderr[-300:]}"
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
            prefix=f"{leased['id']}-",
            dir=work_root,
        ) as temporary:
            local_source = Path(temporary) / f"source{suffix}"
            _copy_source(
                store,
                leased,
                local_source,
                timeout_seconds=int(
                    os.environ.get(
                        "COURSE_TRANSCRIPT_RCLONE_COPY_TIMEOUT_SECONDS",
                        "7200",
                    )
                ),
            )
            if not local_source.is_file() or local_source.stat().st_size <= 0:
                raise PreflightError("rclone 未建立有效的本機來源檔")
            if store.get_job(leased["id"])["status"] in {"cancelled", "cancelling"}:
                raise PreflightCancelled("任務已由使用者取消")
            checksum = _sha256(local_source)
            probe = _probe(local_source)
            strategy = record.get("processing_strategy") or DEFAULT_PROCESSING_STRATEGY
            estimate = estimate_job_cost(
                probe["duration_seconds"],
                CostConfig.from_env().for_processing_strategy(strategy),
            )
        if store.get_job(leased["id"])["status"] in {"cancelled", "cancelling"}:
            raise PreflightCancelled("任務已由使用者取消")
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
    except PreflightCancelled:
        try:
            store.release_lease(leased["id"], worker_id)
        except JobConflict:
            pass
        return store.get_job(leased["id"])
    except Exception as exc:
        current = store.get_job(leased["id"])
        if current["status"] in {"cancelled", "cancelling"}:
            return current
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
        write_service_heartbeat(data_dir, "preflight-worker", state="once")
        run_once(store, data_dir=data_dir, worker_id=worker_id)
        return 0
    while True:
        write_service_heartbeat(data_dir, "preflight-worker")
        worked = run_once(store, data_dir=data_dir, worker_id=worker_id)
        if not worked:
            time.sleep(max(1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
