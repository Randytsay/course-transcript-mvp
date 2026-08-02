"""Compatibility-safe Chirp orchestration using hardened submit/recovery modules."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.providers import run_chirp_pipeline as base
from app.providers.hardening_common import window_matches

_ACTIVE_STATUSES = {
    "SUBMITTED",
    "RUNNING",
    "RECOVERING",
    "CANCEL_REQUESTED",
    "SUCCEEDED",
    "EMPTY_SILENCE",
}


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _retained_manifest_exists() -> bool:
    for path in (base.JOB / "chunks").glob("chunk-*/manifest.json"):
        if str(_load_json(path).get("status") or "") in _ACTIVE_STATUSES:
            return True
    return False


def _stored_plan(total_seconds: float) -> tuple[list[tuple[int, float, float]], bool] | None:
    path = base.JOB / "chunk-plan.json"
    payload = _load_json(path)
    if not payload or not _retained_manifest_exists():
        return None
    try:
        stored_duration = float(payload["duration_seconds"])
        chunks = payload["chunks"]
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("retained chunk plan is invalid")
    if abs(stored_duration - total_seconds) > 1.0:
        raise RuntimeError("retained chunk plan belongs to different audio content")
    if not isinstance(chunks, list) or not chunks:
        raise RuntimeError("retained chunk plan has no chunks")
    plan: list[tuple[int, float, float]] = []
    for item in chunks:
        if not isinstance(item, dict):
            raise RuntimeError("retained chunk plan contains invalid entries")
        plan.append(
            (
                int(item["chunk_index"]),
                int(item["source_start_ms"]) / 1000,
                int(item["source_end_ms"]) / 1000,
            )
        )
    strategy = str(payload.get("processing_strategy") or "")
    dynamic = strategy == "DYNAMIC_BATCHING"
    if strategy not in {"DYNAMIC_BATCHING", "PROCESSING_STRATEGY_UNSPECIFIED"}:
        raise RuntimeError("retained chunk plan has unknown processing strategy")
    return plan, dynamic


def _prepare_plan() -> list[tuple[int, float, float]]:
    normalized = base.JOB / "normalized.flac"
    if not normalized.exists():
        try:
            source = base._source_path()
        except RuntimeError as exc:
            print(f"PIPELINE=FAIL {exc}")
            return []
        print("Building normalized.flac (16kHz mono) ...")
        base.normalize_audio(source, normalized)
    total_seconds = base.audio_duration_seconds(normalized)
    retained = _stored_plan(total_seconds)
    if retained is not None:
        plan, dynamic = retained
        base.DYNAMIC_BATCHING = dynamic
        print(
            f"Retaining verified chunk plan ({len(plan)} chunks, "
            f"dynamic={dynamic})"
        )
        return plan
    plan = base.compute_chunk_plan(total_seconds)
    base._write_plan(plan, total_seconds)
    print(
        f"Chunk plan ({len(plan)} chunks, submit={base.MAX_PARALLEL_CHUNKS}, "
        f"recover={base.MAX_PARALLEL_RECOVERY}, dynamic={base.DYNAMIC_BATCHING}):"
    )
    for index, start, end in plan:
        print(f"  chunk-{index:03d}: {start:.1f}s → {end:.1f}s ({end - start:.0f}s)")
    return plan


def _manifest(index: int) -> dict[str, object]:
    return _load_json(base.CHUNKS / f"chunk-{index:03d}" / "manifest.json")


def _compatible(index: int, start: float, end: float) -> bool:
    manifest = _manifest(index)
    if not manifest:
        return True
    return window_matches(
        manifest,
        start_seconds=start,
        end_seconds=end,
        dynamic_batching=base.DYNAMIC_BATCHING,
    )


def _env(index: int, start: float, end: float) -> dict[str, str]:
    values = base._chunk_env(index, start, end)
    values["CHIRP_DYNAMIC_BATCHING"] = (
        "true" if base.DYNAMIC_BATCHING else "false"
    )
    return base.env_with(values)


def submit_chunk(index: int, start: float, end: float) -> tuple[int, bool, str]:
    if not _compatible(index, start, end):
        return index, False, f"chunk-{index:03d}: incompatible retained operation"
    status = str(_manifest(index).get("status") or "")
    if status in _ACTIVE_STATUSES:
        return index, True, f"chunk-{index:03d}: retained {status}"
    env = _env(index, start, end)
    env["SUBMIT_ONLY"] = "1"
    result = base.run_subprocess(
        "app.providers.chirp_chunk_hardened",
        env,
        timeout=900,
    )
    message = (result.stdout or "").strip()
    if result.returncode != 0:
        return index, False, f"{message}\n{(result.stderr or '')[:500]}".strip()
    return index, True, message


def recover_chunk_once(index: int, start: float, end: float) -> tuple[int, str, str]:
    if not _compatible(index, start, end):
        return index, "failed", f"chunk-{index:03d}: incompatible retained operation"
    status = str(_manifest(index).get("status") or "")
    if status in {"SUCCEEDED", "EMPTY_SILENCE"}:
        return index, "done", f"chunk-{index:03d}: already {status}"
    if status in {"", "FAILED", "CANCELLED"}:
        return index, "failed", f"chunk-{index:03d}: no recoverable operation ({status})"
    env = _env(index, start, end)
    env["ALLOW_PENDING"] = "1"
    result = base.run_subprocess(
        "app.providers.recover_chunk_hardened",
        env,
        timeout=900,
    )
    message = (result.stdout or "").strip()
    if result.returncode == 0:
        return index, "done", message
    if result.returncode in {75, 76}:
        return index, "pending", message or f"chunk-{index:03d}: pending"
    return index, "failed", f"{message}\n{(result.stderr or '')[:500]}".strip()


def main() -> int:
    base._prepare_plan = _prepare_plan
    base.submit_chunk = submit_chunk
    base.recover_chunk_once = recover_chunk_once
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
