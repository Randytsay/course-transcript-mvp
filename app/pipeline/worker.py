"""Sequential approved-job worker for the paid transcription pipeline.

The worker never selects an unapproved job. It preserves raw provider evidence,
uses one global source lease, resumes from durable artifacts, and keeps review
state after optionally publishing selected QA-passed outputs beside the source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.jobs.costs import CostConfig, estimate_job_cost
from app.jobs.artifacts import cleanup_completed_audio
from app.jobs.drive_publish import DrivePublishError, publish_outputs, source_parent_destination
from app.jobs.store import JobConflict, JobStore
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY


class PipelineError(RuntimeError):
    pass


class PipelinePaused(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_error(value: str) -> str:
    value = re.sub(
        r"(?i)(token|authorization|credential|private[_ -]?key)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        value,
    )
    return value[-1200:]


def _command_failure_message(
    returncode: int,
    stdout: str,
    stderr: str,
) -> str:
    """Keep the actual command failure while retaining useful diagnostics.

    Python libraries such as jieba emit normal startup information on stderr.
    Selecting only stderr previously hid a provider's real stdout failure
    marker (for example ``BUILD=FAIL invalid fixed segments``).
    """
    details = [f"command exited {returncode}"]
    if stdout.strip():
        details.append(f"stdout:\n{stdout.strip()}")
    if stderr.strip():
        details.append(f"stderr:\n{stderr.strip()}")
    return _safe_error("\n".join(details))


def _check_disk(data_dir: Path, source_size_bytes: int) -> None:
    minimum_free_gb = float(
        os.environ.get("COURSE_TRANSCRIPT_MINIMUM_FREE_SPACE_GB", "3")
    )
    free_bytes = shutil.disk_usage(data_dir).free
    required = int(minimum_free_gb * 1024**3) + int(source_size_bytes * 1.25)
    if free_bytes < required:
        raise PipelineError(
            f"VPS 可用空間不足；需保留至少 {minimum_free_gb:g} GB 安全空間"
        )


def _run_with_heartbeat(
    command: list[str],
    *,
    store: JobStore,
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
    )
    started = time.monotonic()
    last_heartbeat = started
    while process.poll() is None:
        now = time.monotonic()
        if now - started > timeout_seconds:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            raise PipelineError("階段執行超過安全期限")
        if now - last_heartbeat >= 15:
            heartbeat = store.heartbeat(job_id, worker_id, lease_seconds=300)
            if heartbeat["status"] == "paused":
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise PipelinePaused("任務已由使用者暫停")
            last_heartbeat = now
        time.sleep(1)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise PipelineError(_command_failure_message(process.returncode, stdout, stderr))
    return stdout.strip()


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _artifact_evidence(job_dir: Path) -> list[dict[str, Any]]:
    names = (
        "transcript_raw.txt",
        "transcript_corrected.txt",
        "transcript_timestamped.txt",
        "transcript.srt",
        "transcript.vtt",
        "transcript.json",
        "transcript.csv",
        "transcript.docx",
        "transcript.pdf",
        "glossary_candidates.csv",
        "glossary_decisions.yaml",
        "join_qa.json",
        "subtitles-cleaned.json",
        "cleanup-review.json",
        "qa_report.json",
        "qa_report.html",
        "usage_report.json",
    )
    evidence = []
    for name in names:
        path = job_dir / name
        if not path.is_file() or path.stat().st_size <= 0:
            raise PipelineError(f"缺少必要輸出：{name}")
        # A complete read-back catches truncated or unreadable local files.
        with path.open("rb") as stream:
            while stream.read(1024 * 1024):
                pass
        evidence.append(
            {
                "name": name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "source": "Chirp timing with optional fixed-segment correction",
            }
        )
    return evidence


def _source_file(job_dir: Path, source_name: str) -> Path:
    suffix = Path(source_name).suffix.lower() or ".media"
    return job_dir / f"source-original{suffix}"


def _begin(
    store: JobStore,
    record: dict[str, Any],
    worker_id: str,
    *,
    stage: str,
    status: str,
    detail: str,
    progress: int,
) -> None:
    if store.get_job(record["id"])["status"] == "paused":
        raise PipelinePaused("任務已由使用者暫停")
    store.begin_stage(
        job_id=record["id"],
        stage=stage,
        status=status,
        detail=detail,
        progress=progress,
        input_checksum=record.get("source_checksum"),
        worker_id=worker_id,
    )


def _complete(
    store: JobStore,
    job_id: str,
    worker_id: str,
    *,
    stage: str,
    detail: str,
    progress: int,
) -> None:
    store.complete_stage(
        job_id=job_id,
        stage=stage,
        detail=detail,
        progress=progress,
        worker_id=worker_id,
    )


def _download_source(
    store: JobStore,
    record: dict[str, Any],
    data_dir: Path,
    worker_id: str,
) -> Path:
    job_dir = data_dir / "jobs" / record["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    source = _source_file(job_dir, record["source_name"])
    normalized = job_dir / "normalized.flac"
    _begin(
        store,
        record,
        worker_id,
        stage="download",
        status="downloading",
        detail="從 Drive 唯讀下載來源並驗證 SHA-256",
        progress=10,
    )
    if normalized.exists():
        _complete(
            store,
            record["id"],
            worker_id,
            stage="download",
            detail="已存在驗證後的正規化音訊，無須重複下載",
            progress=14,
        )
        return source
    if source.exists():
        checksum = _sha256(source)
    else:
        _check_disk(data_dir, int(record["source_size_bytes"]))
        partial = source.with_suffix(source.suffix + ".partial")
        partial.unlink(missing_ok=True)
        _run_with_heartbeat(
            [
                "rclone",
                "copyto",
                "--immutable",
                record["source_path"],
                str(partial),
            ],
            store=store,
            job_id=record["id"],
            worker_id=worker_id,
            timeout_seconds=int(
                os.environ.get(
                    "COURSE_TRANSCRIPT_RCLONE_COPY_TIMEOUT_SECONDS", "7200"
                )
            ),
        )
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise PipelineError("rclone 未建立有效的本機來源副本")
        checksum = _sha256(partial)
        if checksum != record["source_checksum"]:
            raise PipelineError("來源內容已變更；SHA-256 與 preflight 不一致")
        partial.replace(source)
    if checksum != record["source_checksum"]:
        raise PipelineError("本機來源 SHA-256 與 preflight 不一致")
    _atomic_json(
        job_dir / "source-manifest.json",
        {
            "job_id": record["id"],
            "source_name": record["source_name"],
            "source_sha256": checksum,
            "source_size_bytes": int(record["source_size_bytes"]),
            "drive_source_read_only": True,
        },
    )
    _complete(
        store,
        record["id"],
        worker_id,
        stage="download",
        detail="來源副本與 preflight SHA-256 一致",
        progress=14,
    )
    return source


def _normalize(
    store: JobStore,
    record: dict[str, Any],
    source: Path,
    data_dir: Path,
    worker_id: str,
) -> None:
    job_dir = data_dir / "jobs" / record["id"]
    normalized = job_dir / "normalized.flac"
    _begin(
        store,
        record,
        worker_id,
        stage="normalize",
        status="normalizing",
        detail="建立 16 kHz 單聲道 FLAC",
        progress=15,
    )
    if not normalized.exists():
        if not source.exists():
            raise PipelineError("來源副本與正規化音訊皆不存在")
        temporary = job_dir / "normalized.tmp.flac"
        temporary.unlink(missing_ok=True)
        _run_with_heartbeat(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "flac",
                str(temporary),
            ],
            store=store,
            job_id=record["id"],
            worker_id=worker_id,
            timeout_seconds=7200,
        )
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise PipelineError("FFmpeg 未產生有效正規化音訊")
        temporary.replace(normalized)
    source.unlink(missing_ok=True)
    _complete(
        store,
        record["id"],
        worker_id,
        stage="normalize",
        detail="正規化音訊完成；本機來源副本已清理",
        progress=20,
    )


def _module_env(record: dict[str, Any], job_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    limit = int(env.get("CHIRP_MAX_PARALLEL_CHUNKS_LIMIT", "5"))
    effective_parallelism = min(record.get("chirp_max_parallel_chunks", 3), limit)
    env.update(
        {
            "JOB_NAME": record["id"],
            "SOURCE_MEDIA_PATH": str(_source_file(job_dir, record["source_name"])),
            "LANGUAGE_CODE": record["language_code"],
            "REQUIRE_CORRECTION": (
                "1" if record["enable_gemini_correction"] else "0"
            ),
            "CHIRP_MAX_PARALLEL_CHUNKS": str(effective_parallelism),
            "CONTENT_MODE": str(record.get("content_mode") or "legacy_unspecified"),
            "DOCUMENT_CONTEXT": str(record.get("document_context") or ""),
        }
    )
    return env


def _run_module_stage(
    store: JobStore,
    record: dict[str, Any],
    data_dir: Path,
    worker_id: str,
    *,
    stage: str,
    status: str,
    detail: str,
    progress_start: int,
    progress_end: int,
    module: str,
    timeout_seconds: int,
    evidence: tuple[str, ...],
) -> None:
    job_dir = data_dir / "jobs" / record["id"]
    _begin(
        store,
        record,
        worker_id,
        stage=stage,
        status=status,
        detail=detail,
        progress=progress_start,
    )
    if not all((job_dir / item).exists() for item in evidence):
        _run_with_heartbeat(
            [sys.executable, "-m", module],
            store=store,
            job_id=record["id"],
            worker_id=worker_id,
            timeout_seconds=timeout_seconds,
            env=_module_env(record, job_dir),
        )
    if not all((job_dir / item).exists() for item in evidence):
        raise PipelineError(f"{stage} 完成後缺少必要證據")
    _complete(
        store,
        record["id"],
        worker_id,
        stage=stage,
        detail=f"{detail}完成",
        progress=progress_end,
    )


def _record_usage_evidence(
    store: JobStore,
    record: dict[str, Any],
    data_dir: Path,
    worker_id: str,
) -> None:
    strategy = record.get("processing_strategy") or DEFAULT_PROCESSING_STRATEGY
    estimate = estimate_job_cost(
        float(record["duration_seconds"]),
        CostConfig.from_env().for_processing_strategy(strategy),
    )
    store.record_usage(
        job_id=record["id"],
        dedupe_key="chirp-base-audio",
        provider="google-cloud-speech",
        model="chirp_3",
        input_units=round(estimate.chirp_billable_minutes * 60),
        output_units=None,
        estimated_cost_usd=estimate.chirp_usd,
        usage={
            "unit": "estimated_billable_audio_seconds",
            "source": "chunk-plan.json",
            "accounting_note": "Application estimate; Cloud Billing is authoritative.",
        },
        worker_id=worker_id,
    )
    usage_records = [
        {
            "provider": "google-cloud-speech",
            "model": "chirp_3",
            "estimated_cost_usd": str(estimate.chirp_usd),
            "unit": "estimated_billable_audio_seconds",
            "input_units": round(estimate.chirp_billable_minutes * 60),
        }
    ]
    if not record["enable_gemini_correction"]:
        _atomic_json(
            data_dir / "jobs" / record["id"] / "usage_report.json",
            {
                "accounting_note": (
                    "Application estimate only; Cloud Billing is authoritative."
                ),
                "records": usage_records,
            },
        )
        return
    job_dir = data_dir / "jobs" / record["id"]
    evidence = [job_dir / "glossary" / "global-terms.json"]
    evidence.extend(sorted((job_dir / "correction-v2").glob("*.json")))
    prompt_tokens = 0
    output_tokens = 0
    raw_usage: list[dict[str, Any]] = []
    for path in evidence:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        usage = payload.get("usage_metadata") or {}
        prompt_tokens += int(
            usage.get("prompt_token_count")
            or usage.get("input_token_count")
            or 0
        )
        output_tokens += int(
            usage.get("candidates_token_count")
            or usage.get("output_token_count")
            or 0
        )
        raw_usage.append(usage)
    config = CostConfig.from_env()
    gemini_estimate = (
        Decimal(prompt_tokens)
        * config.gemini_input_usd_per_million_tokens
        / Decimal("1000000")
        + Decimal(output_tokens)
        * config.gemini_output_usd_per_million_tokens
        / Decimal("1000000")
    ).quantize(Decimal("0.0001"))
    store.record_usage(
        job_id=record["id"],
        dedupe_key="gemini-segment-correction",
        provider="google-vertex-ai",
        model="gemini-3.7-flash",
        input_units=prompt_tokens,
        output_units=output_tokens,
        estimated_cost_usd=gemini_estimate,
        usage={
            "unit": "tokens",
            "records": len(raw_usage),
            "accounting_note": "Application estimate; Cloud Billing is authoritative.",
        },
        worker_id=worker_id,
    )
    usage_records.append(
        {
            "provider": "google-vertex-ai",
            "model": "gemini-3.7-flash",
            "estimated_cost_usd": str(gemini_estimate),
            "unit": "tokens",
            "input_units": prompt_tokens,
            "output_units": output_tokens,
        }
    )
    _atomic_json(
        job_dir / "usage_report.json",
        {
            "accounting_note": (
                "Application estimate only; Cloud Billing is authoritative."
            ),
            "records": usage_records,
        },
    )


def _auto_publish_to_source(
    store: JobStore,
    record: dict[str, Any],
    data_dir: Path,
    worker_id: str,
) -> dict[str, Any] | None:
    """Publish only derived, user-selected files after all local QA succeeds."""
    if os.environ.get("COURSE_TRANSCRIPT_AUTO_PUBLISH_TO_SOURCE", "").lower() not in {
        "1", "true", "yes"
    }:
        return None
    _begin(
        store,
        record,
        worker_id,
        stage="drive_publish",
        status="exporting",
        detail="將選定輸出寫回原始 Drive 資料夾",
        progress=99,
    )
    job_dir = data_dir / "jobs" / record["id"]
    last_heartbeat = 0.0

    def keep_lease_alive(seconds: float) -> None:
        nonlocal last_heartbeat
        remaining = seconds
        while remaining > 0:
            now = time.monotonic()
            if now - last_heartbeat >= 10:
                heartbeat = store.heartbeat(record["id"], worker_id, lease_seconds=300)
                if heartbeat["status"] == "paused":
                    raise PipelinePaused("任務已由使用者暫停")
                last_heartbeat = now
            interval = min(1.0, remaining)
            time.sleep(interval)
            remaining -= interval

    state = publish_outputs(
        job_dir,
        source_name=record["source_name"],
        destination=source_parent_destination(record["source_path"]),
        output_formats=json.loads(record["output_formats_json"]),
        authorized=True,
        sleeper=keep_lease_alive,
    )
    _complete(
        store,
        record["id"],
        worker_id,
        stage="drive_publish",
        detail="已驗證輸出至原始 Drive 資料夾",
        progress=100,
    )
    return state


def run_paid_job(
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
    fake_provider = os.environ.get("COURSE_TRANSCRIPT_FAKE_PROVIDER", "").lower() in {
        "1",
        "true",
        "yes",
    }
    try:
        source = _download_source(store, leased, data_dir, worker_id)
        _normalize(store, leased, source, data_dir, worker_id)
        _run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="chirp",
            status="transcribing",
            detail="Chirp 3 字詞時間軸辨識",
            progress_start=21,
            progress_end=62,
            module=(
                "app.providers.fake_timeline"
                if fake_provider
                else "app.providers.run_chirp_pipeline"
            ),
            timeout_seconds=14_400,
            evidence=("chunk-plan.json", "merged-words.json"),
        )
        _run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="segment",
            status="segmenting",
            detail="依 Chirp 時間軸建立固定字幕段",
            progress_start=63,
            progress_end=72,
            module="app.providers.build_srt",
            timeout_seconds=600,
            evidence=("subtitles.json", "subtitles.srt", "subtitles.vtt"),
        )
        if leased["enable_gemini_correction"]:
            _run_module_stage(
                store,
                leased,
                data_dir,
                worker_id,
                stage="correction",
                status="correcting",
                detail="Gemini 3.7 Flash 固定 segment 純文字校正",
                progress_start=73,
                progress_end=88,
                module=(
                    "app.providers.fake_correction"
                    if fake_provider
                    else "app.providers.correct_text"
                ),
                timeout_seconds=14_400,
                evidence=(
                    "glossary/global-terms.json",
                    "subtitles-corrected.json",
                    "review-terms.json",
                ),
            )
        _run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="cleanup",
            status="quality_check",
            detail="自動清理贅字、邊界語助詞與明顯口吃並建立複核清單",
            progress_start=89,
            progress_end=90,
            module="app.providers.subtitle_cleanup",
            timeout_seconds=600,
            evidence=(
                "subtitles-cleaned.json",
                "subtitles-cleaned.srt",
                "transcript-cleaned.txt",
                "cleanup-review.json",
            ),
        )
        _run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="export",
            status="exporting",
            detail="產生本機多格式輸出",
            progress_start=90,
            progress_end=94,
            module="app.providers.export_formats",
            timeout_seconds=600,
            evidence=("transcript-segments.csv", "export-manifest.json"),
        )
        _run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="qa",
            status="quality_check",
            detail="執行本機輸出與結構 QA",
            progress_start=95,
            progress_end=98,
            module="app.providers.qa_report",
            timeout_seconds=600,
            evidence=("qa-report.json", "qa-report.md", "density-retry-plan.json"),
        )
        _run_module_stage(
            store,
            leased,
            data_dir,
            worker_id,
            stage="validation",
            status="quality_check",
            detail="驗證所有本機輸出與原始證據",
            progress_start=98,
            progress_end=99,
            module="app.providers.validate_outputs",
            timeout_seconds=600,
            evidence=("qa-report.json", "export-manifest.json"),
        )
        _record_usage_evidence(store, leased, data_dir, worker_id)
        publication = None
        publication_error = None
        try:
            publication = _auto_publish_to_source(store, leased, data_dir, worker_id)
        except DrivePublishError as exc:
            # Publication is derived output delivery, never a reason to lose
            # completed paid ASR evidence or repeat Gemini/Chirp work.
            publication_error = _safe_error(str(exc))
        processing_manifest = {
                "job_id": leased["id"],
                "status": "AWAITING_HUMAN_REVIEW",
                "chirp_model": "chirp_3",
                "correction_model": (
                    "gemini-3.7-flash"
                    if leased["enable_gemini_correction"]
                    else None
                ),
                "drive_upload_started": publication is not None,
                "drive_publication_status": (
                    publication.get("status") if publication else
                    "pending_retry" if publication_error else "not_requested"
                ),
                "drive_publication_error": publication_error,
                "source_media_preserved_in_drive": True,
                "fake_provider": fake_provider,
                "artifacts": _artifact_evidence(job_dir),
            }
        _atomic_json(job_dir / "pipeline-manifest.json", processing_manifest)
        _atomic_json(job_dir / "processing_manifest.json", processing_manifest)
        cleanup_completed_audio(job_dir)
        return store.finish_for_review(
            job_id=leased["id"],
            worker_id=worker_id,
            drive_published=publication is not None,
            drive_publication_error=publication_error,
        )
    except PipelinePaused:
        store.release_lease(leased["id"], worker_id)
        return store.get_job(leased["id"])
    except Exception as exc:
        try:
            current = store.get_job(leased["id"])
            store.fail_job(
                job_id=leased["id"],
                stage=current.get("active_stage") or "pipeline",
                error=_safe_error(str(exc)),
                worker_id=worker_id,
            )
        except JobConflict:
            pass
        raise


def run_once(store: JobStore, *, data_dir: Path, worker_id: str) -> bool:
    record = store.next_paid_job()
    if record is None:
        return False
    try:
        run_paid_job(store, record, data_dir=data_dir, worker_id=worker_id)
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
    worker_id = os.environ.get(
        "COURSE_TRANSCRIPT_PIPELINE_WORKER_ID", "pipeline-worker-1"
    )
    if args.once:
        run_once(store, data_dir=data_dir, worker_id=worker_id)
        return 0
    while True:
        worked = run_once(store, data_dir=data_dir, worker_id=worker_id)
        if not worked:
            time.sleep(max(1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
