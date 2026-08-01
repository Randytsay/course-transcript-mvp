"""Observed pipeline worker with cooperative pause/cancel and performance reports.

This wrapper preserves the reviewed paid pipeline while adding process-group
termination, durable stage-attempt metrics, and best-effort cancellation of
submitted Speech long-running operations.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from app.jobs.cancellation import cancel_chirp_operations, finalize_cancellation
from app.jobs.performance import (
    build_performance_summary,
    ensure_schema,
    record_stage_completed,
    record_stage_started,
    record_stage_stopped,
    write_performance_reports,
)
from app.pipeline import worker as base

_ORIGINAL_BEGIN = base._begin
_ORIGINAL_COMPLETE = base._complete
_ORIGINAL_RUN_PAID_JOB = base.run_paid_job
_CURRENT_STAGE: dict[str, str] = {}


def _database_path(data_dir: Path) -> Path:
    return data_dir / "course-transcript.db"


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=5)


def _run_with_heartbeat(
    command: list[str],
    *,
    store: Any,
    job_id: str,
    worker_id: str,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> str:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    started = time.monotonic()
    last_heartbeat = started
    last_state_check = started
    while process.poll() is None:
        now = time.monotonic()
        if now - started > timeout_seconds:
            _terminate_process_group(process)
            raise base.PipelineError("階段執行超過安全期限")
        if now - last_state_check >= 2:
            status = str(store.get_job(job_id)["status"])
            if status in {"paused", "cancelling", "cancelled"}:
                _terminate_process_group(process)
                raise base.PipelinePaused(
                    "任務已由使用者暫停"
                    if status == "paused"
                    else "任務已由使用者取消"
                )
            last_state_check = now
        if now - last_heartbeat >= 15:
            heartbeat = store.heartbeat(job_id, worker_id, lease_seconds=300)
            if heartbeat["status"] in {"paused", "cancelling", "cancelled"}:
                _terminate_process_group(process)
                raise base.PipelinePaused("任務已由使用者停止")
            last_heartbeat = now
        time.sleep(0.5)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise base.PipelineError(
            base._command_failure_message(process.returncode, stdout, stderr)
        )
    return stdout.strip()


def _begin(
    store: Any,
    record: dict[str, Any],
    worker_id: str,
    *,
    stage: str,
    status: str,
    detail: str,
    progress: int,
) -> None:
    current_status = str(store.get_job(record["id"])["status"])
    if current_status in {"cancelling", "cancelled"}:
        raise base.PipelinePaused("任務已由使用者取消")
    _ORIGINAL_BEGIN(
        store,
        record,
        worker_id,
        stage=stage,
        status=status,
        detail=detail,
        progress=progress,
    )
    _CURRENT_STAGE[record["id"]] = stage
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    record_stage_started(_database_path(data_dir), record["id"], stage)


def _complete(
    store: Any,
    job_id: str,
    worker_id: str,
    *,
    stage: str,
    detail: str,
    progress: int,
) -> None:
    _ORIGINAL_COMPLETE(
        store,
        job_id,
        worker_id,
        stage=stage,
        detail=detail,
        progress=progress,
    )
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    record_stage_completed(_database_path(data_dir), job_id, stage)
    if _CURRENT_STAGE.get(job_id) == stage:
        _CURRENT_STAGE.pop(job_id, None)


def _write_report_safely(data_dir: Path, job_id: str) -> None:
    try:
        summary = build_performance_summary(
            _database_path(data_dir),
            data_dir,
            job_id,
        )
        write_performance_reports(data_dir / "jobs" / job_id, summary)
    except Exception:
        # Observability must never turn a completed/cancelled job into a failure.
        return


def run_paid_job(
    store: Any,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    database_path = _database_path(data_dir)
    ensure_schema(database_path)
    try:
        result = _ORIGINAL_RUN_PAID_JOB(
            store,
            record,
            data_dir=data_dir,
            worker_id=worker_id,
        )
        current = store.get_job(record["id"])
        active_stage = _CURRENT_STAGE.get(record["id"]) or current.get("active_stage")
        if current["status"] == "cancelling":
            record_stage_stopped(
                database_path,
                record["id"],
                active_stage,
                status="cancelled",
            )
            provider_results = cancel_chirp_operations(
                data_dir / "jobs" / record["id"]
            )
            result = finalize_cancellation(
                database_path,
                data_dir,
                job_id=record["id"],
                worker_id=worker_id,
                provider_results=provider_results,
            )
        elif current["status"] == "paused":
            record_stage_stopped(
                database_path,
                record["id"],
                active_stage,
                status="paused",
            )
        _CURRENT_STAGE.pop(record["id"], None)
        _write_report_safely(data_dir, record["id"])
        return result
    except Exception as exc:
        try:
            current = store.get_job(record["id"])
            active_stage = _CURRENT_STAGE.get(record["id"]) or current.get("active_stage")
            record_stage_stopped(
                database_path,
                record["id"],
                active_stage,
                status="failed",
                error=str(exc),
            )
        finally:
            _CURRENT_STAGE.pop(record["id"], None)
            _write_report_safely(data_dir, record["id"])
        raise


# Patch only the extension points used by the reviewed base worker.
base._run_with_heartbeat = _run_with_heartbeat
base._begin = _begin
base._complete = _complete
base.run_paid_job = run_paid_job


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
