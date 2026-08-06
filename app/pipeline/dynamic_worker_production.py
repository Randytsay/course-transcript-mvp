"""Fully hardened production entrypoint for the paid transcription worker."""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.drive_publish import DrivePublishError
from app.jobs.store import JobConflict, JobStore
from app.pipeline import dynamic_worker_hardened as worker

_ORIGINAL_AUTO_PUBLISH = worker.base._auto_publish_to_source
_ORIGINAL_FINISH_AFTER_CHIRP = worker._finish_after_chirp


def _review_required(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _processing_strategy(job_dir: Path) -> str:
    try:
        payload = json.loads((job_dir / "chunk-plan.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "DYNAMIC_BATCHING"
    strategy = str(payload.get("processing_strategy") or "")
    return (
        strategy
        if strategy in {"DYNAMIC_BATCHING", "PROCESSING_STRATEGY_UNSPECIFIED"}
        else "DYNAMIC_BATCHING"
    )


def _locked_auto_publish(
    store: Any,
    record: dict[str, Any],
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any] | None:
    """Publish only jobs that explicitly opt out of human review."""
    if _review_required(record.get("require_human_review")):
        return None

    source_path = str(record.get("source_path") or "")
    try:
        with drive_publish_lock(data_dir, source_path):
            return _ORIGINAL_AUTO_PUBLISH(
                store,
                record,
                data_dir,
                worker_id,
            )
    except OSError as exc:
        raise DrivePublishError(
            f"Unable to acquire or use the global Drive publication lock: {exc}"
        ) from exc


def _finish_with_actual_strategy(
    store: JobStore,
    leased: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    """Use the retained strategy and enforce the persisted review gate."""
    job_dir = data_dir / "jobs" / leased["id"]
    strategy = _processing_strategy(job_dir)
    previous = os.environ.get("CHIRP_DYNAMIC_BATCHING")
    os.environ["CHIRP_DYNAMIC_BATCHING"] = (
        "true" if strategy == "DYNAMIC_BATCHING" else "false"
    )
    try:
        result = _ORIGINAL_FINISH_AFTER_CHIRP(
            store,
            leased,
            data_dir=data_dir,
            worker_id=worker_id,
        )
    finally:
        if previous is None:
            os.environ.pop("CHIRP_DYNAMIC_BATCHING", None)
        else:
            os.environ["CHIRP_DYNAMIC_BATCHING"] = previous

    awaiting_review = str(result.get("status") or "") == "awaiting_review"
    for name in ("pipeline-manifest.json", "processing_manifest.json"):
        path = job_dir / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["chirp_processing_strategy"] = strategy
            if awaiting_review:
                payload.update(
                    {
                        "status": "AWAITING_REVIEW",
                        "drive_upload_started": False,
                        "drive_publication_status": "awaiting_human_review",
                        "drive_publication_error": None,
                        "human_review_blocking": True,
                        "subtitle_review_status": "pending",
                    }
                )
            worker.base._atomic_json(path, payload)

    if awaiting_review:
        worker.schedule(
            job_dir,
            "awaiting_review",
            detail="human_review_required",
        )
    return result


def _submit_or_resume_chirp(
    store: JobStore,
    record: dict[str, Any],
    *,
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any]:
    """Submit dynamic work or finish a retained standard-batch job safely."""
    leased = store.acquire_lease(record["id"], worker_id, lease_seconds=300)
    if not leased["approved_at"] or Decimal(leased["reserved_cost_usd"]) <= 0:
        store.release_lease(record["id"], worker_id)
        raise JobConflict("未經人工費用確認的任務不可進入付費管線")
    job_dir = data_dir / "jobs" / leased["id"]
    try:
        source = worker.base._download_source(
            store,
            leased,
            data_dir,
            worker_id,
        )
        worker.base._normalize(
            store,
            leased,
            source,
            data_dir,
            worker_id,
        )
        worker.base._begin(
            store,
            leased,
            worker_id,
            stage="chirp",
            status="transcribing",
            detail="提交或恢復 Chirp 3 批次並保存 operation",
            progress=21,
        )
        env = worker.base._module_env(leased, job_dir)
        env.update(
            {
                "CHIRP_DYNAMIC_BATCHING": "true",
                "CHIRP_SUBMIT_ONLY": "1",
            }
        )
        worker.base._run_with_heartbeat(
            [
                sys.executable,
                "-m",
                "app.providers.run_chirp_pipeline_hardened",
            ],
            store=store,
            job_id=leased["id"],
            worker_id=worker_id,
            timeout_seconds=14_400,
            env=env,
        )

        # A retained pre-deployment standard plan ignores submit-only and may
        # complete synchronously. Continue immediately instead of falsely
        # labelling it as a dynamic job waiting for recovery.
        if (job_dir / "merged-words.json").is_file():
            return worker._finish_after_chirp(
                store,
                leased,
                data_dir=data_dir,
                worker_id=worker_id,
            )

        _, total = worker._chunk_counts(job_dir)
        if total <= 0 or not (job_dir / "chirp-submitted.json").is_file():
            raise worker.base.PipelineError(
                "Chirp 動態批次提交後缺少持久化證據"
            )
        worker.schedule(
            job_dir,
            "submitted",
            detail=f"submitted_chunks={total}",
        )
        now = worker.time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            worker.time.gmtime(),
        )
        with store.transaction() as connection:
            store._require_lease(connection, leased["id"], worker_id)
            connection.execute(
                """
                UPDATE jobs
                SET status='transcribing', active_stage='chirp',
                    stage_detail=?, progress=45, updated_at=?,
                    revision=revision+1
                WHERE id=?
                """,
                (
                    f"Chirp 動態批次已提交 {total} 段；等待 Google 離峰處理",
                    now,
                    leased["id"],
                ),
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
    except worker.base.PipelinePaused:
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
                error=worker.base._safe_error(str(exc)),
                worker_id=worker_id,
            )
        except JobConflict:
            pass
        raise


worker.base._auto_publish_to_source = _locked_auto_publish
worker._finish_after_chirp = _finish_with_actual_strategy
worker._submit_job = _submit_or_resume_chirp


if __name__ == "__main__":
    raise SystemExit(worker.main())
