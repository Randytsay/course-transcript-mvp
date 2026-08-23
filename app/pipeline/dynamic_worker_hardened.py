"""Crash-resumable, rate-safe worker for Chirp dynamic batching."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.jobs.artifacts import install_artifact_patch
from app.jobs.cancellation import next_cancelling_job
from app.jobs.completion import install_completion_patch
from app.jobs.drive_publish import DrivePublishError
from app.jobs.performance_enhanced import build_performance_summary as enhanced_performance_summary
from app.jobs.correction_policy import get_job_correction_policy
from app.jobs.store import JobConflict, JobStore
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY, is_dynamic_batching
from app.operations.runtime_heartbeat import write_service_heartbeat
from app.pipeline import worker as base
from app.pipeline import worker_observed as observed
from app.pipeline.recovery_schedule import is_due, schedule
from app.providers.correction_evidence import summarize_routing

install_completion_patch(JobStore)
install_artifact_patch(base)
observed.build_performance_summary = enhanced_performance_summary

# Explicit safe baseline marker used by configuration drift guards; actual per-job
# routing is recorded separately from correction-routing.json.
GEMINI_CORRECTION_BASELINE_MODEL = "gemini-3.7-flash"

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
    data_dir = Path(env.get("COURSE_TRANSCRIPT_DATA_DIR", str(job_dir.parent.parent)))
    try:
        store = JobStore(data_dir / "course-transcript.db")
    except Exception:
        env["CORRECTION_REQUESTED_POLICY"] = "GEMINI_FIRST"
        return env
    try:
        full = store.get_job(record["id"]) if hasattr(store, "get_job") else None
        full = full or record
        provider = (full or {}).get("correction_provider") or ""
        mode = (full or {}).get("correction_execution_mode") or "REALTIME"
        profile_id = (full or {}).get("correction_provider_profile_id") or ""
        model = (full or {}).get("correction_model") or ""
        if provider:
            env["CORRECTION_ROUTER_ENABLED"] = "true"
            env["CORRECTION_PROVIDER"] = provider
            env["CORRECTION_PROVIDER_PROFILE_ID"] = profile_id
            env["CORRECTION_MODEL"] = model
            env["CORRECTION_EXECUTION_MODE"] = mode
            env["CORRECTION_REQUESTED_POLICY"] = f"ROUTER:{provider}:{mode}"
        else:
            env["CORRECTION_ROUTER_ENABLED"] = "false"
            try:
                env["CORRECTION_REQUESTED_POLICY"] = get_job_correction_policy(
                    store, record["id"]
                )
            except Exception:
                env["CORRECTION_REQUESTED_POLICY"] = "GEMINI_FIRST"
    except Exception:
        env["CORRECTION_ROUTER_ENABLED"] = "false"
        env["CORRECTION_REQUESTED_POLICY"] = "GEMINI_FIRST"
    return env


base._module_env = _module_env


def _correction_manifest_fields(job_dir: Path, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"correction_model": None, "correction_policy": None, "correction_initial_provider": None, "correction_route": None, "correction_models_used": [], "correction_segment_counts": {}, "correction_routing_manifest": None}
    summary = summarize_routing(job_dir)
    models = summary.get("correction_models_used", [])
    return {"correction_model": models[0] if isinstance(models, list) and len(models) == 1 else None, **summary}


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
    with closing(store.connect()) as connection:
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
    with closing(store.connect()) as connection:
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


_LOCAL_QA_REPAIR_EVIDENCE = (
    "normalized.flac",
    "merged-words.json",
    "subtitles.json",
    "subtitles.srt",
    "subtitles.vtt",
    "subtitles-corrected.json",
    "review-terms.json",
    "subtitles-cleaned.json",
    "subtitles-cleaned.srt",
    "transcript-cleaned.txt",
    "cleanup-review.json",
    "export-manifest.json",
)
_LOCAL_QA_REPAIR_REPORTS = (
    "qa-report.json",
    "qa-report.md",
    "qa_report.json",
    "qa_report.html",
    "density-retry-plan.json",
    "content-qa.json",
    "export-manifest.json",
)


def _archive_qa_repair_evidence(job_dir: Path) -> str:
    """Copy prior QA/validation output before a local-only recalculation."""
    archive = job_dir / "qa-repair-archives" / (
        time.strftime("repair-%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:8]
    )
    archive.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    for name in _LOCAL_QA_REPAIR_REPORTS:
        source = job_dir / name
        if not source.is_file():
            continue
        shutil.copy2(source, archive / name)
        copied.append(name)
    base._atomic_json(
        archive / "manifest.json",
        {
            "policy": "preserve_previous_qa_before_local_only_repair",
            "copied": copied,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return str(archive.relative_to(job_dir))


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


def _repair_qa_only(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    """Recalculate local QA/validation without calling a paid provider.

    This path is deliberately evidence-gated.  It is used only after Chirp,
    segmentation, correction, cleanup, and export evidence already exist.  A
    missing prerequisite fails closed instead of silently falling back to a
    full paid retry.
    """
    leased = store.acquire_lease(record["id"], worker_id, lease_seconds=300)
    job_dir = data_dir / "jobs" / leased["id"]
    try:
        missing = [
            name
            for name in _LOCAL_QA_REPAIR_EVIDENCE
            if not (job_dir / name).is_file()
        ]
        if missing:
            raise base.PipelineError(
                "QA 本機修復缺少既有證據，已 fail-closed；"
                f"不會自動重跑付費辨識：{', '.join(missing)}"
            )
        archive = _archive_qa_repair_evidence(job_dir)
        base._run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="qa",
            status="quality_check",
            detail="只重算本機 QA（保留既有辨識與校正證據）",
            progress_start=95,
            progress_end=98,
            module="app.providers.qa_report",
            timeout_seconds=600,
            evidence=("qa-report.json", "qa-report.md", "density-retry-plan.json"),
            force=True,
        )
        base._run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="validation",
            status="quality_check",
            detail="只重算本機輸出與結構驗證",
            progress_start=98,
            progress_end=99,
            module="app.providers.validate_outputs_hardened",
            timeout_seconds=600,
            evidence=("qa-report.json", "export-manifest.json", "content-qa.json"),
            force=True,
        )
        base._record_usage_evidence(store, leased, data_dir, worker_id)
        manifest = {
            "job_id": leased["id"],
            "status": "AWAITING_HUMAN_REVIEW",
            "chirp_model": "chirp_3",
            **_correction_manifest_fields(job_dir, bool(leased["enable_gemini_correction"])),
            "drive_upload_started": False,
            "drive_publication_status": "awaiting_human_review",
            "drive_publication_error": None,
            "source_media_preserved_in_drive": True,
            "human_review_blocking": True,
            "subtitle_review_status": "pending",
            "qa_repair_policy": "local_only_no_provider_calls",
            "qa_repair_archive": archive,
            "artifacts": base._artifact_evidence(job_dir),
        }
        base._atomic_json(job_dir / "pipeline-manifest.json", manifest)
        base._atomic_json(job_dir / "processing_manifest.json", manifest)
        base.cleanup_completed_audio(job_dir)
        result = store.finish_for_review(
            job_id=leased["id"],
            worker_id=worker_id,
            drive_published=False,
            drive_publication_error=None,
        )
        store.append_audit_event(
            job_id=leased["id"],
            event_type="local_qa_repair_completed",
            actor=worker_id,
            payload={
                "archive": archive,
                "provider_calls_made": False,
                "drive_mutations_made": False,
            },
        )
        schedule(job_dir, "awaiting_review", detail="local_qa_repair_completed")
        observed._write_report_safely(data_dir, leased["id"])
        return result
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
                stage=current.get("active_stage") or "qa",
                error=base._safe_error(str(exc)),
                worker_id=worker_id,
            )
        except JobConflict:
            pass
        raise


def _next_ai_batch(store: JobStore, data_dir: Path) -> dict[str, Any] | None:
    """Select one submitted AI Batch job without holding a source lease."""
    for record in _available_rows(store, ("waiting_ai_batch",)):
        job_dir = data_dir / "jobs" / record["id"]
        if (job_dir / "subtitles.json").is_file():
            return record
    return None


def _router_context(record: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    from app.providers.correction_runtime_bridge import context_for_job

    return context_for_job(record, data_dir)


def _submit_ai_batch(
    store: JobStore,
    leased: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    """Submit an official correction Batch and release the source lease."""
    from app.providers.correction_runtime_bridge import run_module

    job_dir = data_dir / "jobs" / leased["id"]
    base._begin(
        store,
        leased,
        worker_id,
        stage="correction",
        status="correcting",
        detail="提交官方 AI Batch 並保存 recovery 證據",
        progress=73,
    )
    result = run_module(ctx=_router_context(leased, data_dir))
    if str(result.get("correction_status")) not in {"submitted", "processing"}:
        raise base.PipelineError("AI Batch 提交後未取得可 recovery 的 provider job id")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    schedule(job_dir, "ai-batch-submitted", detail=f"run_id={result.get('correction_run_id')}")
    with store.transaction() as connection:
        store._require_lease(connection, leased["id"], worker_id)
        connection.execute(
            """
            UPDATE jobs
            SET status='waiting_ai_batch', active_stage='correction',
                stage_detail=?, progress=88, updated_at=?, revision=revision+1
            WHERE id=?
            """,
            (
                f"AI Batch 已提交，等待 provider 回收（run_id={result.get('correction_run_id')}）",
                now,
                leased["id"],
            ),
        )
        store._clear_lease(connection, leased["id"], worker_id)
        store._event(
            connection,
            leased["id"],
            "ai_correction_batch_submitted",
            worker_id,
            {
                "run_id": result.get("correction_run_id"),
                "provider_job_id": result.get("correction_provider_job_id"),
                "provider": result.get("correction_provider"),
                "model": result.get("correction_model"),
                "worker_released": True,
            },
        )
    return store.get_job(leased["id"])


def _recover_ai_batch(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    """Poll, persist, strictly ingest, and resume a completed AI Batch."""
    from app.providers.correction.batch_state import AICorrectionRunStore
    from app.providers.correction.orchestrator import build_windows
    from app.providers.correction_runtime_bridge import (
        _atomic_json,
        _build_orchestrator,
        _write_corrected_outputs,
    )

    leased = store.acquire_lease(record["id"], worker_id, lease_seconds=300)
    job_dir = data_dir / "jobs" / leased["id"]
    ctx = _router_context(leased, data_dir)
    run_store = AICorrectionRunStore(lambda: store.transaction())
    runs = [run for run in run_store.for_job(leased["id"])
            if str(run.get("execution_mode")) == "BATCH"]
    run = runs[-1] if runs else None
    try:
        if run is None:
            raise base.PipelineError("waiting_ai_batch 找不到 durable AI correction run")
        body_path = job_dir / "correction-batch-results" / f"run-{run['id']}.json"
        body: dict[str, Any] | None = None
        if run["status"] == "completed":
            body = json.loads(body_path.read_text(encoding="utf-8"))
        elif run["status"] in {"submitted", "processing"}:
            orchestrator = _build_orchestrator(ctx)
            outcomes = orchestrator.poll_pending(
                providers=[str(run["provider"])], finalize=False
            )
            outcome = next((item for item in outcomes if int(item["run_id"]) == int(run["id"])), None)
            if outcome is None or outcome["status"] == "processing":
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                with store.transaction() as connection:
                    store._require_lease(connection, leased["id"], worker_id)
                    connection.execute(
                        "UPDATE jobs SET stage_detail=?, updated_at=?, revision=revision+1 WHERE id=?",
                        ("AI Batch 處理中，已釋放來源 lease 等待下次 recovery", now, leased["id"]),
                    )
                    store._clear_lease(connection, leased["id"], worker_id)
                    store._event(connection, leased["id"], "ai_correction_batch_processing", worker_id, {"run_id": run["id"]})
                return store.get_job(leased["id"])
            if outcome["status"] in {"failed", "cancelled", "expired", "error"}:
                raise base.PipelineError(
                    f"AI Batch recovery 失敗（run_id={run['id']}，status={outcome['status']}）"
                )
            body = outcome.get("body")
            if not isinstance(body, dict):
                raise base.PipelineError("AI Batch 已完成但缺少 provider response body")
            _atomic_json(body_path, body)
            run_store.update_status(run["id"], status="completed")
        else:
            raise base.PipelineError(
                f"AI Batch run {run['id']} 處於不可 recovery 狀態：{run['status']}"
            )

        if body is None:
            raise base.PipelineError("AI Batch recovery 缺少已保存 response body")
        windows = build_windows(ctx["segments"])
        segments_by_window = {window["window_id"]: window["segments"] for window in windows}
        orchestrator = _build_orchestrator(ctx)
        ingested = orchestrator.ingest_completed(
            int(run["id"]), body, segments_by_window
        )
        render_result = {
            **ctx,
            "correction_status": "completed_batch",
            "correction_corrections": ingested["corrections"],
            "correction_prompt_version": ingested["prompt_version"],
        }
        _write_corrected_outputs(ctx, render_result, raw_response=body, audit_status="completed_batch")
        schedule(job_dir, "ai-batch-completed", detail=f"run_id={run['id']}")
        store.append_audit_event(
            job_id=leased["id"],
            event_type="ai_correction_batch_ingested",
            actor=worker_id,
            payload={"run_id": run["id"], "provider": run["provider"], "raw_response_saved": True},
        )
        # The normal completion path is deliberately reused only after the
        # corrected evidence has been durably written. It will skip paid
        # stages and continue with cleanup, export, QA, validation and review.
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
                job_id=leased["id"],
                stage=current.get("active_stage") or "correction",
                error=base._safe_error(str(exc)),
                worker_id=worker_id,
            )
        except JobConflict:
            pass
        observed._write_report_safely(data_dir, leased["id"])
        return store.get_job(leased["id"])


def _finish_after_chirp(
    store: JobStore,
    leased: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    job_dir = data_dir / "jobs" / leased["id"]
    fake_provider = _env_true("COURSE_TRANSCRIPT_FAKE_PROVIDER")
    # Per-job provider router only kicks in when the legacy correction policy
    # env (CORRECTION_REQUESTED_POLICY) is NOT already set for legacy jobs.
    use_router = (
        str(leased.get("correction_provider") or "")
        and not fake_provider
    )
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
        if (
            use_router
            and str(leased.get("correction_execution_mode") or "REALTIME").upper() == "BATCH"
            and not (job_dir / "subtitles-corrected.json").is_file()
        ):
            return _submit_ai_batch(
                store,
                leased,
                data_dir=data_dir,
                worker_id=worker_id,
            )
        base._run_module_stage(
            store, leased, data_dir, worker_id,
            stage="correction", status="correcting",
            detail="固定 segment AI 純文字校正",
            progress_start=73, progress_end=88,
            module=("app.providers.fake_correction"
                    if fake_provider
                    else ("app.providers.correction_runtime_bridge"
                          if use_router else "app.providers.correct_text_hardened")),
            timeout_seconds=14_400,
            evidence=("glossary/global-terms.json", "subtitles-corrected.json", "review-terms.json", "terminology-consistency.json"),
        )
    base._run_module_stage(
        store, leased, data_dir, worker_id,
        stage="cleanup", status="quality_check",
        detail="自動清理贅字、邊界語助詞與明顯口吃並建立複核清單",
        progress_start=89, progress_end=90,
        module="app.providers.subtitle_cleanup", timeout_seconds=600,
        evidence=("subtitles-cleaned.json", "subtitles-cleaned.srt", "transcript-cleaned.txt", "cleanup-review.json"),
    )
    base._run_module_stage(
        store, leased, data_dir, worker_id,
        stage="export", status="exporting",
        detail="產生選定的字幕與逐字稿輸出",
        progress_start=90, progress_end=94,
        module="app.providers.export_formats", timeout_seconds=600,
        evidence=("export-manifest.json",),
    )
    base._run_module_stage(
        store, leased, data_dir, worker_id,
        stage="qa", status="quality_check",
        detail="執行本機輸出與結構 QA",
        progress_start=95, progress_end=98,
        module="app.providers.qa_report", timeout_seconds=600,
        evidence=("qa-report.json", "qa-report.md", "density-retry-plan.json"),
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
        **_correction_manifest_fields(job_dir, bool(leased["enable_gemini_correction"])),
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
    base.cleanup_completed_audio(job_dir)
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
    review_value = str(record.get("require_human_review") or "").strip().lower()
    if (
        str(record.get("active_stage") or "") in {"qa", "validation"}
        and review_value not in {"", "0", "false", "no", "off"}
    ):
        return _repair_qa_only(
            store,
            record,
            data_dir=data_dir,
            worker_id=worker_id,
        )
    if (job_dir / "merged-words.json").is_file():
        leased = store.acquire_lease(record["id"], worker_id, lease_seconds=300)
        return _finish_after_chirp(store, leased, data_dir=data_dir, worker_id=worker_id)
    return _submit_job(store, record, data_dir=data_dir, worker_id=worker_id)


def _record_unhandled_pipeline_failure(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
    error: Exception,
) -> None:
    """Persist a queued/resumed stage failure without taking down the worker."""
    try:
        current = store.get_job(record["id"])
        store.fail_job(
            job_id=record["id"],
            stage=current.get("active_stage") or "pipeline",
            error=base._safe_error(str(error)),
            worker_id=worker_id,
        )
    except (JobConflict, KeyError):
        # The stage may already have released or lost its lease; the worker
        # must remain alive and the existing evidence remains authoritative.
        pass
    observed._write_report_safely(data_dir, record["id"])


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

    ai_batch = _next_ai_batch(store, data_dir)
    if ai_batch is not None:
        try:
            _recover_ai_batch(
                store, ai_batch, data_dir=data_dir, worker_id=worker_id
            )
        except JobConflict:
            return False
        return True

    due = _next_due_waiting(store, data_dir)
    if due is not None:
        try:
            _recover_job(store, due, data_dir=data_dir, worker_id=worker_id)
        except JobConflict:
            # Another source currently owns the single global lease.  This is
            # expected while a Batch operation is in flight; do not crash and
            # restart the worker, just let the next poll retry this record.
            return False
        return True

    resumable = _next_resumable(store, data_dir)
    if resumable is not None:
        try:
            _resume_job(store, resumable, data_dir=data_dir, worker_id=worker_id)
        except JobConflict:
            return False
        except base.PipelineError as exc:
            _record_unhandled_pipeline_failure(
                store, resumable, data_dir=data_dir, worker_id=worker_id, error=exc
            )
        return True

    max_inflight = max(1, int(os.environ.get("CHIRP_DYNAMIC_MAX_INFLIGHT_JOBS", "5")))
    if _waiting_count(store) < max_inflight:
        queued = _next_fresh(store)
        if queued is not None:
            try:
                _submit_job(store, queued, data_dir=data_dir, worker_id=worker_id)
            except JobConflict:
                return False
            except base.PipelineError as exc:
                _record_unhandled_pipeline_failure(
                    store, queued, data_dir=data_dir, worker_id=worker_id, error=exc
                )
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
        write_service_heartbeat(data_dir, "pipeline-worker", state="once")
        run_once(store, data_dir=data_dir, worker_id=worker_id)
        return 0
    while True:
        write_service_heartbeat(data_dir, "pipeline-worker")
        worked = run_once(store, data_dir=data_dir, worker_id=worker_id)
        time.sleep(0.25 if worked else max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
