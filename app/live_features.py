"""Read-only live task features layered over the existing transcription API."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.billing.config import BillingConfig, BillingConfigError
from app.billing.snapshot import snapshot_for_api
from app.jobs.costs import CostConfig
from app.jobs.strategy import DEFAULT_PROCESSING_STRATEGY
from app.jobs.store import JobNotFound, JobStore


router = APIRouter()
DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOBS_DIR = DATA_DIR / "jobs"
TERMINAL_CHUNK_STATES = {"SUCCEEDED", "EMPTY_SILENCE", "FAILED"}
COMMITTED_COST_STATES = {
    "SUBMITTED",
    "RUNNING",
    "RECOVERING",
    "SUCCEEDED",
    "EMPTY_SILENCE",
}
KNOWN_CHUNK_STATES = {
    "WAITING",
    "PLANNED",
    "SUBMITTED",
    "RUNNING",
    "RECOVERING",
    "SUCCEEDED",
    "EMPTY_SILENCE",
    "FAILED",
}

_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|token|credential|private[_ -]?key)\s*[:=]\s*\S+"
)
_GCS_PATTERN = re.compile(r"gs://[^\s]+")
_OPERATION_PATTERN = re.compile(r"projects/[^\s]+/operations/[^\s]+")
_PATH_PATTERN = re.compile(r"(?:/[^\s:]+){2,}")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]$")
_ASCII_START = re.compile(r"^[A-Za-z0-9]")
_NO_SPACE_BEFORE = set("，。！？；：、,.!?;:%％)]}〉》」』】）…")
_NO_SPACE_AFTER = set("([{〈《「『【（")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_job_dir(job_id: str) -> Path:
    if Path(job_id).name != job_id or job_id in {"", ".", ".."}:
        raise HTTPException(status_code=404, detail="Job not found")
    directory = JOBS_DIR / job_id
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")
    return directory


def _store() -> JobStore:
    return JobStore(DATA_DIR / "course-transcript.db")


def _job_record(job_id: str) -> dict[str, Any]:
    try:
        return _store().get_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


def safe_chunk_error(value: object) -> str | None:
    if value in {None, ""}:
        return None
    if isinstance(value, dict):
        code = value.get("code")
        message = value.get("message") or value.get("detail") or "Chunk failed"
        text = f"{code}: {message}" if code not in {None, ""} else str(message)
    else:
        text = str(value)
    text = _SECRET_PATTERN.sub(r"\1=[REDACTED]", text)
    text = _GCS_PATTERN.sub("gs://[REDACTED]", text)
    text = _OPERATION_PATTERN.sub("operation/[REDACTED]", text)
    text = _PATH_PATTERN.sub("/[REDACTED]", text)
    return text[-300:]


def normalize_chunk_status(value: object) -> str:
    status = str(value or "WAITING").upper()
    if status == "PLANNED":
        return "WAITING"
    return status if status in KNOWN_CHUNK_STATES else "WAITING"


def words_to_text(words: list[dict[str, Any]]) -> str:
    """Render mixed Chinese, Latin tokens, numbers, and punctuation readably."""
    output = ""
    previous = ""
    for item in words:
        token = str(item.get("word") or "").strip()
        if not token:
            continue
        needs_space = False
        if output and token[0] not in _NO_SPACE_BEFORE and previous[-1:] not in _NO_SPACE_AFTER:
            previous_ascii = bool(_ASCII_WORD.search(previous))
            current_ascii = bool(_ASCII_START.search(token))
            previous_cjk = bool(_CJK.search(previous[-1:]))
            current_cjk = bool(_CJK.search(token[:1]))
            needs_space = (
                (previous_ascii and current_ascii)
                or (previous_ascii and current_cjk)
                or (previous_cjk and current_ascii)
            )
        if needs_space:
            output += " "
        output += token
        previous = token
    return output.strip()


def _chunk_plan(job_dir: Path) -> list[dict[str, Any]]:
    payload = _read_json(job_dir / "chunk-plan.json", {})
    chunks = payload.get("chunks") if isinstance(payload, dict) else None
    plan: list[dict[str, Any]] = []
    if isinstance(chunks, list):
        for item in chunks:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item["chunk_index"])
                start_ms = int(item["source_start_ms"])
                end_ms = int(item["source_end_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            if index < 0 or start_ms < 0 or end_ms <= start_ms:
                continue
            plan.append(
                {
                    "chunkIndex": index,
                    "startMs": start_ms,
                    "endMs": end_ms,
                }
            )
    if plan:
        return sorted(plan, key=lambda item: item["chunkIndex"])

    # Recovery fallback for jobs created before chunk-plan persistence.
    for manifest_path in sorted((job_dir / "chunks").glob("chunk-*/manifest.json")):
        manifest = _read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            continue
        try:
            index = int(manifest["chunk_index"])
            start_ms = int(manifest["source_start_ms"])
            end_ms = int(manifest["source_end_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        if end_ms > start_ms:
            plan.append(
                {
                    "chunkIndex": index,
                    "startMs": start_ms,
                    "endMs": end_ms,
                }
            )
    return sorted(plan, key=lambda item: item["chunkIndex"])


def _manifest(job_dir: Path, index: int) -> dict[str, Any]:
    payload = _read_json(
        job_dir / "chunks" / f"chunk-{index:03d}" / "manifest.json", {}
    )
    return payload if isinstance(payload, dict) else {}


def _partial_from_words(job_dir: Path, plan_item: dict[str, Any]) -> dict[str, Any] | None:
    index = int(plan_item["chunkIndex"])
    chunk_dir = job_dir / "chunks" / f"chunk-{index:03d}"
    manifest = _manifest(job_dir, index)
    status = normalize_chunk_status(manifest.get("status"))
    if status not in {"SUCCEEDED", "EMPTY_SILENCE"}:
        return None
    words_payload = _read_json(chunk_dir / "words.json", {})
    words = words_payload.get("words") if isinstance(words_payload, dict) else None
    if not isinstance(words, list):
        return None
    normalized_words = [item for item in words if isinstance(item, dict)]
    raw_text = words_to_text(normalized_words)
    completed_at = manifest.get("created_at")
    if not completed_at:
        try:
            completed_at = datetime.fromtimestamp(
                (chunk_dir / "words.json").stat().st_mtime, tz=UTC
            ).isoformat()
        except OSError:
            completed_at = datetime.now(UTC).isoformat()
    payload = {
        "chunkIndex": index,
        "sourceStartMs": int(plan_item["startMs"]),
        "sourceEndMs": int(plan_item["endMs"]),
        "status": status,
        "wordCount": len(normalized_words),
        "rawText": raw_text,
        "firstWordMs": int(normalized_words[0].get("start_ms", 0))
        if normalized_words
        else None,
        "lastWordMs": int(normalized_words[-1].get("end_ms", 0))
        if normalized_words
        else None,
        "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "completedAt": str(completed_at),
    }
    _atomic_json(chunk_dir / "partial-transcript.json", payload)
    return payload


def build_chunk_progress(job_id: str) -> dict[str, Any]:
    record = _job_record(job_id)
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        return {
            "jobId": job_id,
            "jobStatus": record["status"],
            "completedCount": 0,
            "totalCount": 0,
            "parallelism": int(record.get("chirp_max_parallel_chunks") or 3),
            "canaryCompleted": False,
            "updatedAt": record.get("updated_at"),
            "chunks": [],
        }
    plan = _chunk_plan(job_dir)
    chunks: list[dict[str, Any]] = []
    completed_count = 0
    canary_completed = False
    for item in plan:
        index = int(item["chunkIndex"])
        manifest = _manifest(job_dir, index)
        status = normalize_chunk_status(manifest.get("status"))
        partial_path = (
            job_dir / "chunks" / f"chunk-{index:03d}" / "partial-transcript.json"
        )
        if status in {"SUCCEEDED", "EMPTY_SILENCE"}:
            completed_count += 1
            if index == 0:
                canary_completed = True
            _partial_from_words(job_dir, item)
        updated_at = manifest.get("created_at")
        if not updated_at:
            try:
                updated_at = datetime.fromtimestamp(
                    partial_path.stat().st_mtime, tz=UTC
                ).isoformat()
            except OSError:
                updated_at = record.get("updated_at")
        chunks.append(
            {
                "chunkIndex": index,
                "startMs": int(item["startMs"]),
                "endMs": int(item["endMs"]),
                "durationMs": int(item["endMs"]) - int(item["startMs"]),
                "status": status,
                "wordCount": int(manifest.get("word_count") or 0),
                "hasTranscript": (
                    status == "SUCCEEDED" and partial_path.is_file()
                ),
                "updatedAt": updated_at,
                "error": safe_chunk_error(manifest.get("error"))
                if status == "FAILED"
                else None,
            }
        )
    return {
        "jobId": job_id,
        "jobStatus": record["status"],
        "completedCount": completed_count,
        "totalCount": len(plan),
        "parallelism": int(record.get("chirp_max_parallel_chunks") or 3),
        "canaryCompleted": canary_completed,
        "updatedAt": record.get("updated_at"),
        "chunks": chunks,
    }


def get_chunk_transcript(job_id: str, chunk_index: int) -> dict[str, Any]:
    if chunk_index < 0:
        raise HTTPException(status_code=404, detail="Chunk not found")
    job_dir = _safe_job_dir(job_id)
    plan_item = next(
        (
            item
            for item in _chunk_plan(job_dir)
            if int(item["chunkIndex"]) == chunk_index
        ),
        None,
    )
    if plan_item is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    manifest = _manifest(job_dir, chunk_index)
    status = normalize_chunk_status(manifest.get("status"))
    if status not in {"SUCCEEDED", "EMPTY_SILENCE"}:
        raise HTTPException(status_code=409, detail="Chunk transcript is not ready")
    partial = _partial_from_words(job_dir, plan_item)
    if partial is None:
        if status == "EMPTY_SILENCE":
            partial = {
                "chunkIndex": chunk_index,
                "sourceStartMs": int(plan_item["startMs"]),
                "sourceEndMs": int(plan_item["endMs"]),
                "status": status,
                "wordCount": 0,
                "rawText": "",
                "completedAt": manifest.get("created_at"),
            }
        else:
            raise HTTPException(status_code=404, detail="Chunk transcript is unavailable")
    return {
        "chunkIndex": partial["chunkIndex"],
        "startMs": partial["sourceStartMs"],
        "endMs": partial["sourceEndMs"],
        "status": partial["status"],
        "wordCount": partial["wordCount"],
        "rawText": partial["rawText"],
        "completedAt": partial.get("completedAt"),
        "isFinal": False,
        "warning": (
            "此為 Chirp 分段原始稿，尚未完成重疊接合、Gemini 校正及最終 QA。"
        ),
    }


def _usage_tokens(job_dir: Path) -> tuple[int, int]:
    prompt_tokens = 0
    output_tokens = 0
    evidence: list[Path] = []
    glossary = job_dir / "glossary" / "global-terms.json"
    if glossary.is_file():
        evidence.append(glossary)
    for correction_dir in ("correction-v2", "correction-cascade-v1", "correction-m3-v1"):
        evidence.extend(sorted((job_dir / correction_dir).glob("*.json")))
    seen: set[Path] = set()
    for path in evidence:
        if path in seen:
            continue
        seen.add(path)
        payload = _read_json(path, {})
        usage = payload.get("usage_metadata") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            continue
        prompt_tokens += int(
            usage.get("prompt_token_count")
            or usage.get("input_token_count")
            or usage.get("input_tokens")
            or usage.get("promptTokenCount")
            or usage.get("inputTokenCount")
            or 0
        )
        output_tokens += int(
            usage.get("candidates_token_count")
            or usage.get("output_token_count")
            or usage.get("output_tokens")
            or usage.get("candidatesTokenCount")
            or usage.get("outputTokenCount")
            or 0
        )
    return prompt_tokens, output_tokens


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_UP))


def build_live_cost(job_id: str) -> dict[str, Any]:
    record = _job_record(job_id)
    job_dir = JOBS_DIR / job_id
    config = CostConfig.from_env().for_processing_strategy(
        record.get("processing_strategy") or DEFAULT_PROCESSING_STRATEGY
    )
    total_estimated = Decimal(str(record.get("estimated_cost_usd") or "0"))
    committed_seconds = Decimal("0")
    chirp_cost = Decimal("0")
    submitted_operations: set[str] = set()
    completed_count = 0
    if job_dir.is_dir():
        # Reuse the durable report accounting so retries and targeted patches
        # have the same totals in the operator UI and exported performance log.
        from app.jobs.performance import _chunk_metrics

        for item in _chunk_metrics(job_dir, config):
            status = normalize_chunk_status(str(item.get("status") or ""))
            if item.get("countedAsSubmitted"):
                operation_key = str(item.get("operationName") or f"{item.get('chunkIndex')}:{item.get('attemptId', 'root')}")
                if operation_key not in submitted_operations:
                    submitted_operations.add(operation_key)
                    committed_seconds += Decimal(str(item.get("billedAudioSeconds") or 0))
                    chirp_cost += Decimal(str(item.get("estimatedCostUsd") or 0))
            if status in {"SUCCEEDED", "EMPTY_SILENCE"} and not item.get("attemptId"):
                completed_count += 1
    chirp_cost = chirp_cost.quantize(Decimal("0.0001"), rounding=ROUND_UP)
    prompt_tokens, output_tokens = _usage_tokens(job_dir)
    gemini_cost = (
        Decimal(prompt_tokens)
        * config.gemini_input_usd_per_million_tokens
        / Decimal("1000000")
        + Decimal(output_tokens)
        * config.gemini_output_usd_per_million_tokens
        / Decimal("1000000")
    ).quantize(Decimal("0.0001"), rounding=ROUND_UP)
    accrued = chirp_cost + gemini_cost
    remaining = max(Decimal("0"), total_estimated - accrued)
    return {
        "estimatedTotalUsd": _money(total_estimated),
        "estimatedTotalTwd": str(config.usd_as_twd(total_estimated)),
        "estimatedAccruedUsd": _money(accrued),
        "estimatedAccruedTwd": str(config.usd_as_twd(accrued)),
        "estimatedRemainingUsd": _money(remaining),
        "estimatedRemainingTwd": str(config.usd_as_twd(remaining)),
        "chirpEstimatedUsd": _money(chirp_cost),
        "chirpEstimatedTwd": str(config.usd_as_twd(chirp_cost)),
        "geminiEstimatedUsd": _money(gemini_cost),
        "geminiEstimatedTwd": str(config.usd_as_twd(gemini_cost)),
        "submittedChunkCount": len(submitted_operations),
        "completedChunkCount": completed_count,
        "isEstimate": True,
        "warning": "系統即時估算，Cloud Billing 為最終依據。",
    }


def billing_summary() -> dict[str, Any]:
    try:
        config = BillingConfig.from_env()
    except BillingConfigError:
        return {
            "status": "error",
            "source": "bigquery_standard_billing_export",
            "warning": "帳務設定格式不正確",
            "lastBillingDataAt": None,
            "snapshotGeneratedAt": None,
            "dataAgeSeconds": None,
        }
    return snapshot_for_api(
        config.snapshot_path,
        enabled=config.enabled,
        stale_seconds=config.snapshot_stale_seconds,
    )


@router.get("/api/v1/jobs/{job_id}/chunks")
def chunks_endpoint(job_id: str) -> dict[str, Any]:
    return build_chunk_progress(job_id)


@router.get("/api/v1/jobs/{job_id}/chunks/{chunk_index}/transcript")
def chunk_transcript_endpoint(job_id: str, chunk_index: int) -> dict[str, Any]:
    return get_chunk_transcript(job_id, chunk_index)


@router.get("/api/v1/jobs/{job_id}/live-cost")
def live_cost_endpoint(job_id: str) -> dict[str, Any]:
    return build_live_cost(job_id)


@router.get("/api/v1/billing/summary")
def billing_summary_endpoint() -> dict[str, Any]:
    return billing_summary()
