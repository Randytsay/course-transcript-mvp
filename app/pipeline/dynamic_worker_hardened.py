"""Crash-resumable, rate-safe worker for Chirp dynamic batching."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.jobs.artifacts import install_artifact_patch
from app.jobs.cancellation import next_cancelling_job
from app.jobs.completion import install_completion_patch
from app.jobs.drive_publish import DrivePublishError
from app.jobs.performance_enhanced import build_performance_summary as enhanced_performance_summary
from app.jobs.store import JobConflict, JobStore
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY, is_dynamic_batching
from app.pipeline import worker as base
from app.pipeline import worker_observed as observed
from app.pipeline.recovery_schedule import is_due, schedule

install_completion_patch(JobStore)
install_artifact_patch(base)
observed.build_performance_summary = enhanced_performance_summary

_ORIGINAL_MODULE_ENV = base._module_env
_ACTIVE_RESUMABLE = (
    "downloading",
    "normalizing",
    "transcribing",
    "merging",
    "segmenting",
    "correcting",
    "exporting",
    "quality_check",
)


def _module_env(record: dict[str, Any], job_dir: Path) -> dict[str, str]:
    env = _ORIGINAL_MODULE_ENV(record, job_dir)
    env["OUTPUT_FORMATS_JSON"] = str(
        record.get("output_formats_json") or '["srt","txt"]'
    )
    strategy = record.get("processing_strategy") or DEFAULT_PROCESSING_STRATEGY
    env["CHIRP_DYNAMIC_BATCHING"] = "true" if is_dynamic_batching(strategy) else "false"
    env.setdefault("GEMINI_CORRECTION_WINDOW_MS", "60000")
    return env


base._module_env = _module_env


def _env_true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _terminate(process: subprocess.Popen[str]) -> None:
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


def _run_allow_pending(
    command: list[str],
    *,
    store: JobStore,
    job_id: str,
    worker_id: str,
    timeout_seconds: int,
    env: dict[str, str],
) -> tuple[int, str, str]:
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
            _terminate(process)
            raise base.PipelineError("動態批次回收檢查超過安全期限")
        if now - last_state_check >= 2:
            status = str(store.get_job(job_id)["status"])
            if status in {"paused", "cancelling", "cancelled"}:
                _terminate(process)
                raise base.PipelinePaused("任務已由使用者停止")
            last_state_check = now
        if now - last_heartbeat >= 15:
            heartbeat = store.heartbeat(job_id, worker_id, lease_seconds=300)
            if heartbeat["status"] in {"paused", "cancelling", "cancelled"}:
                _terminate(process)
                raise base.PipelinePaused("任務已由使用者停止")
            last_heartbeat = now
        time.sleep(0.5)
    stdout, stderr = process.communicate()
    return int(process.returncode or 0), stdout.strip(), stderr.strip()


def _available_rows(store: JobStore, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in statuses)
    now = base.datetime.now(base.UTC).isoformat() if hasattr(base, "datetime") else None
    if now is None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
    with store.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM jobs
            WHERE status IN ({placeholders})
              AND approved_at IS NOT NULL
              AND CAST(reserved_cost_usd AS REAL) > 0
              AND (
                    locked_by IS NULL
                    OR lease_expires_at IS NULL
                    OR lease_expires_at < ?
              )
            ORDER BY updated_at, created_at, batch_id, queue_position
            LIMIT 200
            """,
            (*statuses, now),
        ).fetchall()
    return [dict(row) for row in rows]


def _waiting_count(store: JobStore) -> int:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM jobs
            WHERE status = 'transcribing'
              AND active_stage = 'chirp'
              AND approved_at IS NOT NULL
            """
        ).fetchone()
    return int(row["count"] if row else 0)


def _chunk_retry_requested(job_dir: Path) -> bool:
    try:
        payload = json.loads((job_dir / "chirp-retry-request.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and bool(payload.get("chunks"))


def _clear_chunk_retry_request(job_dir: Path) -> None:
    (job_dir / "chirp-retry-request.json").unlink(missing_ok=True)


def _next_due_waiting(store: JobStore, data_dir: Path) -> dict[str, Any] | None:
    for record in _available_rows(store, ("transcribing",)):
        if str(record.get("active_stage") or "") != "chirp":
            continue
        job_dir = data_dir / "jobs" / record["id"]
        if (
            not _chunk_retry_requested(job_dir)
            and (job_dir / "chirp-submitted.json").is_file()
            and is_due(job_dir)
        ):
            return record
    return None


def _next_resumable(store: JobStore, data_dir: Path) -> dict[str, Any] | None:
    for record in _available_rows(store, _ACTIVE_RESUMABLE):
        job_dir = data_dir / "jobs" / record["id"]
        if (
            record.get("status") == "transcribing"
            and record.get("active_stage") == "chirp"
            and (job_dir / "chirp-submitted.json").is_file()
            and not _chunk_retry_requested(job_dir)
        ):
            continue
        return record
    return None


def _next_fresh(store: JobStore) -> dict[str, Any] | None:
    rows = _available_rows(store, ("queued",))
    return rows[0] if rows else None


def _chunk_counts(job_dir: Path) -> tuple[int, int]:
    try:
        plan = json.loads((job_dir / "chunk-plan.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return 0, 0
    chunks = plan.get("chunks") if isinstance(plan, dict) else []
    if not isinstance(chunks, list):
        return 0, 0
    completed = 0
    for item in chunks:
        try:
            index = int(item["chunk_index"])
            manifest = json.loads(
                (job_dir / "chunks" / f"chunk-{index:03d}" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
        except (KeyError, TypeError, ValueError, FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if manifest.get("status") in {"SUCCEEDED", "EMPTY_SILENCE"}:
            completed += 1
    return completed, len(chunks)


def _submit_job(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    leased = store.acquire_lease(record["id"], worker_id, lease_seconds=300)
    if not leased["approved_at"] or Decimal(leased["reserved_cost_usd"]) <= 0:
        store.release_lease(record["id"], worker_id)
        raise JobConflict("未經人工費用確認的任務不可進入付費管線")
    job_dir = data_dir / "jobs" / leased["id"]
    try:
        source = base._download_source(store, leased, data_dir, worker_id)
        base._normalize(store, leased, source, data_dir, worker_id)
        base._begin(
            store,
            leased,
            worker_id,
            stage="chirp",
            status="transcribing",
            detail="提交 Chirp 3 動態批次並保存 operation",
            progress=21,
        )
        env = base._module_env(leased, job_dir)
        env.update({"CHIRP_DYNAMIC_BATCHING": "true", "CHIRP_SUBMIT_ONLY": "1"})
        base._run_with_heartbeat(
            [sys.executable, "-m", "app.providers.run_chirp_pipeline_hardened"],
            store=store,
            job_id=leased["id"],
            worker_id=worker_id,
            timeout_seconds=7_200,
            env=env,
        )
        _, total = _chunk_counts(job_dir)
        if total <= 0 or not (job_dir / "chirp-submitted.json").is_file():
            raise base.PipelineError("Chirp 動態批次提交後缺少持久化證據")
        _clear_chunk_retry_request(job_dir)
        schedule(job_dir, "submitted", detail=f"submitted_chunks={total}")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with store.transaction() as connection:
            store._require_lease(connection, leased["id"], worker_id)
            connection.execute(
                """
                UPDATE jobs
                SET status='transcribing', active_stage='chirp',
                    stage_detail=?, progress=45, updated_at=?, revision=revision+1
                WHERE id=?
                """,
                (f"Chirp 動態批次已提交 {total} 段；等待 Google 離峰處理", now, leased["id"]),
            )
            store._clear_lease(connection, leased["id"], worker_id)
            store._event(
                connection,
                leased["id"],
                "chirp_dynamic_batch_submitted",
                worker_id,
                {"chunk_count": total, "worker_released": True},
            )
        return store.get_job(leased["id"])
    except base.PipelinePaused:
        try:
            store.release_lease(leased["id"], worker_id)
        except JobConflict:
            pass
        return store.get_job(leased["id"])
    except Exception as exc:
        try:
            current = store.get_job(leased["id"])
            store.fail_job(
                job_id=leased["id"],
                stage=current.get("active_stage") or "chirp",
                error=base._safe_error(str(exc)),
                worker_id=worker_id,
            )
        except JobConflict:
            pass
        raise


def _finish_after_chirp(
    store: JobStore,
    leased: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    job_dir = data_dir / "jobs" / leased["id"]
    fake_provider = _env_true("COURSE_TRANSCRIPT_FAKE_PROVIDER")
    if str(leased.get("active_stage") or "") == "chirp":
        base._complete(
            store,
            leased["id"],
            worker_id,
            stage="chirp",
            detail="Chirp 3 動態批次結果已完整回收並合併",
            progress=62,
        )
    base._run_module_stage(
        store, leased, data_dir, worker_id,
        stage="segment", status="segmenting",
        detail="依 Chirp 時間軸建立固定字幕段",
        progress_start=63, progress_end=72,
        module="app.providers.build_srt", timeout_seconds=600,
        evidence=("subtitles.json", "subtitles.srt", "subtitles.vtt"),
    )
    if leased["enable_gemini_correction"]:
        base._run_module_stage(
            store, leased, data_dir, worker_id,
            stage="correction", status="correcting",
            detail="Gemini 3.6 Flash 固定 segment 純文字校正",
            progress_start=73, progress_end=88,
            module=("app.providers.fake_correction" if fake_provider else "app.providers.correct_text_hardened"),
            timeout_seconds=14_400,
            evidence=("glossary/global-terms.json", "subtitles-corrected.json", "review-terms.json"),
        )
    base._run_module_stage(
        store, leased, data_dir, worker_id,
        stage="export", status="exporting",
        detail="產生選定的字幕與逐字稿輸出",
        progress_start=89, progress_end=94,
        module="app.providers.export_formats", timeout_seconds=600,
        evidence=("export-manifest.json",),
    )
    base._run_module_stage(
        store, leased, data_dir, worker_id,
        stage="qa", status="quality_check",
        detail="執行本機輸出與結構 QA",
        progress_start=95, progress_end=98,
        module="app.providers.qa_report", timeout_seconds=600,
        evidence=("qa-report.json", "qa-report.md"),
    )
    base._run_module_stage(
        store, leased, data_dir, worker_id,
        stage="validation", status="quality_check",
        detail="驗證選定輸出、內容漂移與原始證據",
        progress_start=98, progress_end=99,
        module="app.providers.validate_outputs_hardened", timeout_seconds=600,
        evidence=("qa-report.json", "export-manifest.json", "content-qa.json"),
    )
    base._record_usage_evidence(store, leased, data_dir, worker_id)
    publication = None
    publication_error = None
    try:
        publication = base._auto_publish_to_source(store, leased, data_dir, worker_id)
    except DrivePublishError as exc:
        publication_error = base._safe_error(str(exc))
    manifest = {
        "job_id": leased["id"],
        "status": "COMPLETED",
        "chirp_model": "chirp_3",
        "chirp_processing_strategy": "DYNAMIC_BATCHING",
        "correction_model": "gemini-3.6-flash" if leased["enable_gemini_correction"] else None,
        "drive_upload_started": publication is not None,
        "drive_publication_status": publication.get("status") if publication else "pending_retry" if publication_error else "not_requested",
        "drive_publication_error": publication_error,
        "source_media_preserved_in_drive": True,
        "human_review_blocking": False,
        "subtitle_review_status": "not_reviewed",
        "fake_provider": fake_provider,
        "artifacts": base._artifact_evidence(job_dir),
    }
    base._atomic_json(job_dir / "pipeline-manifest.json", manifest)
    base._atomic_json(job_dir / "processing_manifest.json", manifest)
    result = store.finish_for_review(
        job_id=leased["id"], worker_id=worker_id,
        drive_published=publication is not None,
        drive_publication_error=publication_error,
    )
    schedule(job_dir, "completed")
    observed._write_report_safely(data_dir, leased["id"])
    return result


def _recover_job(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    leased = store.acquire_lease(record["id"], worker_id, lease_seconds=300)
    job_dir = data_dir / "jobs" / leased["id"]
    env = base._module_env(leased, job_dir)
    env.update({
        "CHIRP_DYNAMIC_BATCHING": (
            "true"
            if is_dynamic_batching(leased.get("processing_strategy") or DEFAULT_PROCESSING_STRATEGY)
            else "false"
        ),
        "CHIRP_RECOVER_ONCE": "1",
    })
    try:
        returncode, stdout, stderr = _run_allow_pending(
            [sys.executable, "-m", "app.providers.run_chirp_pipeline_hardened"],
            store=store, job_id=leased["id"], worker_id=worker_id,
            timeout_seconds=3_600, env=env,
        )
        if stdout:
            print(stdout)
        completed, total = _chunk_counts(job_dir)
        if returncode in {75, 76}:
            outcome = "retryable" if returncode == 76 else "pending"
            schedule(job_dir, outcome, detail=base._safe_error(stderr or stdout))
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with store.transaction() as connection:
                store._require_lease(connection, leased["id"], worker_id)
                connection.execute(
                    "UPDATE jobs SET stage_detail=?, progress=?, updated_at=?, revision=revision+1 WHERE id=?",
                    (f"Chirp 動態批次等待中：{completed}/{total} 分段完成", min(61, 45 + round(16 * completed / max(1, total))), now, leased["id"]),
                )
                store._clear_lease(connection, leased["id"], worker_id)
            return store.get_job(leased["id"])
        if returncode != 0:
            schedule(job_dir, "terminal", detail=base._safe_error(stderr or stdout))
            raise base.PipelineError(base._command_failure_message(returncode, stdout, stderr))
        if not (job_dir / "merged-words.json").is_file():
            raise base.PipelineError("Chirp 回收成功但缺少 merged-words.json")
        return _finish_after_chirp(store, leased, data_dir=data_dir, worker_id=worker_id)
    except base.PipelinePaused:
        try:
            store.release_lease(leased["id"], worker_id)
        except JobConflict:
            pass
        return store.get_job(leased["id"])
    except Exception as exc:
        try:
            current = store.get_job(leased["id"])
            store.fail_job(
                job_id=leased["id"], stage=current.get("active_stage") or "chirp",
                error=base._safe_error(str(exc)), worker_id=worker_id,
            )
        except JobConflict:
            pass
        observed._write_report_safely(data_dir, leased["id"])
        return store.get_job(leased["id"])


def _resume_job(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    job_dir = data_dir / "jobs" / record["id"]
    if _chunk_retry_requested(job_dir):
        return _submit_job(store, record, data_dir=data_dir, worker_id=worker_id)
    if (job_dir / "merged-words.json").is_file():
        leased = store.acquire_lease(record["id"], worker_id, lease_seconds=300)
        return _finish_after_chirp(store, leased, data_dir=data_dir, worker_id=worker_id)
    return _submit_job(store, record, data_dir=data_dir, worker_id=worker_id)


def run_once(store: JobStore, *, data_dir: Path, worker_id: str) -> bool:
    cancellation = next_cancelling_job(data_dir / "course-transcript.db")
    if cancellation is not None:
        try:
            observed._finalize_cancelling_job(
                store, cancellation, data_dir=data_dir, worker_id=worker_id
            )
        except JobConflict:
            pass
        return True

    due = _next_due_waiting(store, data_dir)
    if due is not None:
        _recover_job(store, due, data_dir=data_dir, worker_id=worker_id)
        return True

    resumable = _next_resumable(store, data_dir)
    if resumable is not None:
        _resume_job(store, resumable, data_dir=data_dir, worker_id=worker_id)
        return True

    max_inflight = max(1, int(os.environ.get("CHIRP_DYNAMIC_MAX_INFLIGHT_JOBS", "5")))
    if _waiting_count(store) < max_inflight:
        queued = _next_fresh(store)
        if queued is not None:
            _submit_job(store, queued, data_dir=data_dir, worker_id=worker_id)
            return True
    return False


def main() -> int:
    if _env_true("COURSE_TRANSCRIPT_FAKE_PROVIDER"):
        return observed.main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30)
    args = parser.parse_args()
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    store = JobStore(data_dir / "course-transcript.db")
    worker_id = os.environ.get("COURSE_TRANSCRIPT_PIPELINE_WORKER_ID", "pipeline-worker-1")
    if args.once:
        run_once(store, data_dir=data_dir, worker_id=worker_id)
        return 0
    while True:
        worked = run_once(store, data_dir=data_dir, worker_id=worker_id)
        time.sleep(0.25 if worked else max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
