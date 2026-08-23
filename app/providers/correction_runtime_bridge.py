"""Production bridge for per-job AI correction routing.

The hardened pipeline invokes correction providers in a subprocess for
REALTIME jobs. Official BATCH jobs are submitted by the same bridge, but
their completion is resumed by ``dynamic_worker_hardened`` after the worker
lease is released. Raw subtitle segments and provider responses are always
written separately from the derived corrected layer.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


DEFAULT_MODELS = {
    "vertex": "gemini-3.7-flash",
    "openrouter": "google/gemini-3.7-flash",
    "minimax": "MiniMax-M3",
}


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, payload: Any) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _source_payload(job_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    path = job_dir / "subtitles.json"
    raw_payload = _read_json(path, None)
    if not isinstance(raw_payload, dict) or not isinstance(raw_payload.get("segments"), list):
        raise RuntimeError("校正前缺少有效 subtitles.json")
    raw_segments = raw_payload["segments"]
    if not raw_segments or any(not isinstance(item, dict) for item in raw_segments):
        raise RuntimeError("校正前字幕段為空或格式錯誤")
    segments: list[dict[str, Any]] = []
    for item in raw_segments:
        segment_id = str(item.get("segment_id") or "")
        raw_text = str(item.get("raw_text", item.get("text", "")))
        if not segment_id or not raw_text:
            raise RuntimeError("校正前字幕段缺少 segment_id 或 raw_text")
        segments.append({
            **item,
            "segment_id": segment_id,
            "text": raw_text,
            "raw_text": raw_text,
            "start_ms": int(item["start_ms"]),
            "end_ms": int(item["end_ms"]),
        })
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return raw_payload, segments, digest


def _load_glossary(job_dir: Path) -> list[dict[str, Any]]:
    payload = _read_json(job_dir / "glossary" / "global-terms.json", {})
    terms = payload.get("terms", []) if isinstance(payload, dict) else []
    return [term for term in terms if isinstance(term, dict)]


def context_for_job(record: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Build the same provider context for a worker and for this module CLI."""
    job_dir = Path(data_dir) / "jobs" / str(record["id"])
    _raw_payload, segments, source_revision = _source_payload(job_dir)
    return {
        "job_id": str(record["id"]),
        "data_dir": str(data_dir),
        "correction_provider": str(record.get("correction_provider") or ""),
        "correction_provider_profile_id": str(record.get("correction_provider_profile_id") or ""),
        "correction_model": str(record.get("correction_model") or ""),
        "correction_execution_mode": str(record.get("correction_execution_mode") or "REALTIME"),
        "correction_fallback_policy": str(record.get("correction_fallback_policy") or "RAW_CHIRP_FALLBACK"),
        "source_revision": source_revision,
        "source_sha256": source_revision,
        "segments": segments,
        "raw_segments": segments,
        "glossary": _load_glossary(job_dir),
    }


def context_from_environment() -> dict[str, Any]:
    from app.jobs.store import JobStore

    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    job_id = os.environ.get("JOB_NAME", "").strip()
    if not job_id:
        raise RuntimeError("校正橋接器缺少 JOB_NAME")
    record = JobStore(data_dir / "course-transcript.db").get_job(job_id)
    return context_for_job(record, data_dir)


def _build_orchestrator(ctx: dict[str, Any]):
    from app.providers.correction.orchestrator import CorrectionOrchestrator

    return CorrectionOrchestrator(
        run_store=_open_run_store(ctx),
        client_factory=_make_client_factory(ctx),
    )


def run_module(*, ctx: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one correction attempt without writing derived artifacts."""
    provider = str(ctx.get("correction_provider") or "").strip()
    if not provider:
        raise RuntimeError(
            "correction_runtime_bridge invoked without a per-job provider; "
            "legacy jobs must use correct_text_hardened"
        )

    from app.providers.correction.base import ProviderError
    from app.providers.correction.orchestrator import JobCorrectionSpec

    mode = str(ctx.get("correction_execution_mode") or "REALTIME").upper()
    if mode not in {"REALTIME", "BATCH"}:
        raise ProviderError("unknown", f"不支援的 AI 校正模式：{mode}")
    model = str(ctx.get("correction_model") or DEFAULT_MODELS.get(provider, ""))
    spec = JobCorrectionSpec(
        job_id=str(ctx["job_id"]),
        provider=provider,
        provider_profile_id=str(ctx.get("correction_provider_profile_id") or ""),
        model=model,
        execution_mode=mode,
        fallback_policy=str(ctx.get("correction_fallback_policy") or "RAW_CHIRP_FALLBACK"),
        source_revision=str(ctx.get("source_revision") or ""),
        source_sha256=str(ctx.get("source_sha256") or ""),
    )
    orchestrator = _build_orchestrator(ctx)
    segments = list(ctx.get("segments") or [])
    glossary = list(ctx.get("glossary") or [])

    if mode == "BATCH":
        result = orchestrator.submit_batch(spec, segments, glossary)
        return {
            **ctx,
            "correction_status": result["status"],
            "correction_run_id": result["run_id"],
            "correction_provider_job_id": result["provider_job_id"],
            "correction_resubmitted": result["resubmitted"],
            "correction_job_status": "waiting_ai_batch",
            "correction_model": model,
            "lease_released": True,
        }

    try:
        result = orchestrator.correct_realtime(spec, segments, glossary)
        return {
            **ctx,
            "correction_status": "completed_realtime",
            "correction_corrections": result["corrections"],
            "correction_raw_response": result.get("raw_response"),
            "correction_prompt_version": result["prompt_version"],
            "correction_model": model,
            "correction_fallback_segment_ids": list(result.get("fallback_segment_ids") or []),
            "correction_window_results": list(result.get("window_results") or []),
            "correction_provider_circuit_opened": bool(result.get("provider_circuit_opened", False)),
        }
    except ProviderError as exc:
        if str(ctx.get("correction_fallback_policy") or "RAW_CHIRP_FALLBACK") == "RAW_CHIRP_FALLBACK":
            return {
                **ctx,
                "correction_status": "fallback_raw_chirp",
                "correction_error_kind": exc.kind,
                "correction_error_safe_message": exc.safe_message,
                "correction_corrections": [],
                "correction_model": model,
                "correction_fallback_segment_ids": [
                    str(item.get("segment_id")) for item in segments if item.get("segment_id")
                ],
                "correction_window_results": [],
                "correction_provider_circuit_opened": False,
            }
        raise


def _write_glossary(job_dir: Path, terms: list[dict[str, Any]], source_sha256: str,
                    model: str, provider: str) -> None:
    glossary = job_dir / "glossary"
    previous = _read_json(glossary / "global-terms.json", {})
    previous = previous if isinstance(previous, dict) else {}
    _atomic_json(glossary / "global-terms.json", {
        "provider": provider,
        "model": model,
        "prompt_version": "router-glossary-v1",
        "source_sha256": source_sha256,
        "usage_metadata": previous.get("usage_metadata", {}),
        "terms": terms,
        "raw_response": previous.get("raw_response"),
        "cache_hit": True,
        "note": "Provider Router uses existing glossary only; no separate paid terminology request",
    })
    stream_path = glossary / "global-terms.csv"
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = stream_path.with_suffix(stream_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["canonical", "variants", "confidence"])
        writer.writeheader()
        for term in terms:
            writer.writerow({
                "canonical": term.get("canonical", ""),
                "variants": " | ".join(str(value) for value in term.get("variants", [])),
                "confidence": term.get("confidence", ""),
            })
    temporary.replace(stream_path)


def _write_corrected_outputs(ctx: dict[str, Any], result: dict[str, Any], *,
                             raw_response: Any = None,
                             audit_status: str | None = None) -> None:
    from app.providers.correct_text import timestamp, write_review_terms
    from app.providers.terminology_consistency import run_terminology_consistency

    job_dir = Path(ctx["data_dir"]) / "jobs" / str(ctx["job_id"])
    raw_segments = list(ctx.get("raw_segments") or ctx.get("segments") or [])

    # run_module uses the explicit correction_* namespace. Keep compatibility
    # with callers that pass the orchestrator result directly, but never drop
    # valid routed corrections because of a key-name mismatch.
    correction_items = result.get("correction_corrections")
    if correction_items is None:
        correction_items = result.get("corrections", [])
    corrections = {
        str(item.get("segment_id")): item
        for item in correction_items
        if isinstance(item, dict)
    }

    global_fallback = str(result.get("correction_status")) == "fallback_raw_chirp"
    fallback_ids = {
        str(value) for value in result.get("correction_fallback_segment_ids", [])
        if value is not None
    }
    final: list[dict[str, Any]] = []
    for raw in raw_segments:
        segment_id = str(raw["segment_id"])
        raw_text = str(raw.get("raw_text", raw.get("text", "")))
        correction = corrections.get(segment_id, {})
        segment_fallback = global_fallback or segment_id in fallback_ids
        corrected_text = (
            raw_text
            if segment_fallback
            else str(correction.get("corrected_text") or raw_text)
        )
        final.append({
            **raw,
            "corrected_text": corrected_text,
            "text": corrected_text,
            "uncertain_terms": (
                [] if segment_fallback
                else list(correction.get("uncertain_terms") or [])
            ),
            "corrected": corrected_text != raw_text,
            "correction_fallback": segment_fallback,
            "fallback_to_raw": segment_fallback,
        })

    expected = [str(item["segment_id"]) for item in raw_segments]
    if [str(item["segment_id"]) for item in final] != expected:
        raise RuntimeError("校正輸出 segment 順序與原始 subtitles 不一致")

    provider = str(ctx.get("correction_provider") or "")
    model = str(ctx.get("correction_model") or DEFAULT_MODELS.get(provider, ""))
    terms = list(ctx.get("glossary") or [])
    source_sha256 = str(ctx.get("source_sha256") or "")
    _write_glossary(job_dir, terms, source_sha256, model, provider)

    window_results = list(result.get("correction_window_results") or [])
    circuit_opened = bool(result.get("correction_provider_circuit_opened", False))
    payload = {
        "source": "chirp_3_merged + provider_router text-only correction",
        "provider": provider,
        "model": model,
        "execution_mode": str(ctx.get("correction_execution_mode") or "REALTIME"),
        "prompt_version": result.get("correction_prompt_version", "corr-v2"),
        "segment_count": len(final),
        "corrected_count": sum(bool(item["corrected"]) for item in final),
        "fallback_count": sum(bool(item["correction_fallback"]) for item in final),
        "fallback_segment_ids": sorted(fallback_ids),
        "window_results": window_results,
        "provider_circuit_opened": circuit_opened,
        "total_duration_ms": final[-1]["end_ms"],
        "chirp_raw_immutable": True,
        "timestamps_immutable": True,
        "segments": final,
    }
    _atomic_json(job_dir / "subtitles-corrected.json", payload)
    _atomic_text(
        job_dir / "subtitles-corrected.srt",
        "\n\n".join(
            f"{index}\n{timestamp(item['start_ms'])} --> {timestamp(item['end_ms'])}\n{item['corrected_text']}"
            for index, item in enumerate(final, 1)
        ) + "\n",
    )
    _atomic_text(
        job_dir / "subtitles-corrected.vtt",
        "WEBVTT\n\n" + "\n\n".join(
            f"{timestamp(item['start_ms'], '.')} --> {timestamp(item['end_ms'], '.')}\n{item['corrected_text']}"
            for item in final
        ) + "\n",
    )
    _atomic_text(
        job_dir / "transcript-corrected.txt",
        "\n".join(item["corrected_text"] for item in final) + "\n",
    )
    _atomic_text(
        job_dir / "transcript-corrected.md",
        "# 校正逐字稿\n\n" + "\n".join(
            f"[{timestamp(item['start_ms'])[:-4]}] {item['corrected_text']}" for item in final
        ) + "\n",
    )

    import app.providers.correct_text as correct_text
    previous_job = correct_text.JOB
    correct_text.JOB = job_dir
    try:
        write_review_terms(final, terms)
    finally:
        correct_text.JOB = previous_job
    run_terminology_consistency(job_dir)

    audit_dir = job_dir / "correction-v2"
    audit_name = (
        f"router-{str(ctx.get('correction_execution_mode') or 'REALTIME').lower()}-"
        f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
    )
    _atomic_json(audit_dir / audit_name, {
        "provider": provider,
        "model": model,
        "prompt_version": result.get("correction_prompt_version", "corr-v2"),
        "status": audit_status or result.get("correction_status", "completed_realtime"),
        "source_sha256": source_sha256,
        "source_segments": raw_segments,
        "raw_response": raw_response,
        "error_kind": result.get("correction_error_kind"),
        "safe_error": result.get("correction_error_safe_message"),
        "fallback_segment_ids": sorted(fallback_ids),
        "window_results": window_results,
        "provider_circuit_opened": circuit_opened,
        "segments": final,
        "chirp_raw_immutable": True,
        "timestamps_immutable": True,
    })


def main() -> int:
    """CLI contract used by ``worker._run_module_stage``."""
    try:
        ctx = context_from_environment()
        result = run_module(ctx=ctx)
        status = str(result.get("correction_status") or "")
        if status in {"submitted", "processing"}:
            _atomic_json(
                Path(ctx["data_dir"]) / "jobs" / str(ctx["job_id"]) / "ai-correction-submission.json",
                {key: result.get(key) for key in (
                    "job_id", "correction_run_id", "correction_provider_job_id",
                    "correction_provider", "correction_model", "source_revision",
                    "correction_execution_mode", "correction_resubmitted",
                )},
            )
            print(f"CORRECTION=PENDING run_id={result.get('correction_run_id')}")
            return 75
        if str(result.get("correction_execution_mode") or "REALTIME").upper() == "BATCH":
            raise RuntimeError("已完成的 BATCH 必須由 recovery worker ingest，不可由 CLI 直接繼續")
        _write_corrected_outputs(
            ctx,
            result,
            raw_response=result.get("correction_raw_response"),
            audit_status=status,
        )
        print(f"CORRECTION=PASS provider={result.get('correction_provider')} status={status}")
        return 0
    except Exception as exc:
        print(f"CORRECTION=FAIL {type(exc).__name__}: {str(exc)[-500:]}")
        return 1


def _open_run_store(ctx: dict[str, Any]):
    from app.jobs.store import JobStore
    from app.providers.correction.batch_state import AICorrectionRunStore

    store = JobStore(Path(ctx["data_dir"]) / "course-transcript.db")
    return AICorrectionRunStore(lambda: store.transaction())


def _make_client_factory(ctx: dict[str, Any]):
    from app.providers.correction.registry import AIProviderProfileStore

    profiles_root = Path(os.environ.get("AI_PROVIDER_PROFILES_DIR", "/run/ai-providers"))
    selected_model = str(ctx.get("correction_model") or "").strip()

    def factory(provider: str, profile_id: str):
        if provider == "vertex":
            from app.providers.correction.vertex import VertexCorrectionProvider
            return VertexCorrectionProvider(model=selected_model or DEFAULT_MODELS["vertex"])
        return AIProviderProfileStore(profiles_root).build_client(
            profile_id, model=selected_model or None)

    return factory


if __name__ == "__main__":
    raise SystemExit(main())
