"""Dedicated worker for explicit paid single-chunk Chirp retranscription candidates.

This worker never mutates accepted ASR artifacts. It submits/recover operations
inside ``jobs/<job>/retranscription-candidates/<candidate>/`` and writes a local
comparison after recovery. A separate explicit apply operation is required to
change accepted transcript data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.jobs.retranscription_candidates import (
    RetranscriptionCandidateStore,
    chunk_source_sha256,
)
from app.jobs.retranscription_compare import write_candidate_comparison
from app.jobs.store import JobConflict, JobStore
from app.jobs.strategy import DYNAMIC_BATCHING

_PENDING_EXIT = 75
_RETRYABLE_EXIT = 76


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_dir(data_dir: Path, row: dict[str, Any]) -> Path:
    relpath = str(row["candidate_relpath"])
    parts = Path(relpath).parts
    if len(parts) != 2 or parts[0] != "retranscription-candidates" or ".." in parts:
        raise RuntimeError("invalid retranscription candidate path")
    return Path(data_dir) / "jobs" / str(row["job_id"]) / relpath


def _chunk_window(job_dir: Path, chunk_index: int) -> tuple[int, int]:
    plan = _read_json(job_dir / "chunk-plan.json", {})
    chunks = plan.get("chunks", []) if isinstance(plan, dict) else []
    if isinstance(chunks, list):
        for item in chunks:
            if not isinstance(item, dict):
                continue
            try:
                if int(item.get("chunk_index", -1)) == int(chunk_index):
                    start_ms = int(item["source_start_ms"])
                    end_ms = int(item["source_end_ms"])
                    if end_ms > start_ms:
                        return start_ms, end_ms
            except (KeyError, TypeError, ValueError):
                continue
    manifest = _read_json(
        job_dir / "chunks" / f"chunk-{chunk_index:03d}" / "manifest.json", {}
    )
    try:
        start_ms = int(manifest["source_start_ms"])
        end_ms = int(manifest["source_end_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("unable to resolve accepted chunk window") from exc
    if end_ms <= start_ms:
        raise RuntimeError("accepted chunk window is invalid")
    return start_ms, end_ms


def _prepare_candidate_root(data_dir: Path, row: dict[str, Any]) -> tuple[Path, Path]:
    job_dir = Path(data_dir) / "jobs" / str(row["job_id"])
    normalized = job_dir / "normalized.flac"
    if not normalized.is_file():
        raise RuntimeError("source job normalized.flac is missing")
    candidate = _candidate_dir(data_dir, row)
    candidate.mkdir(parents=True, exist_ok=True)
    linked = candidate / "normalized.flac"
    if linked.exists() or linked.is_symlink():
        if not linked.is_symlink() or linked.resolve() != normalized.resolve():
            raise RuntimeError("candidate normalized.flac link is incompatible")
    else:
        linked.symlink_to(Path("..") / ".." / "normalized.flac")
    request_path = candidate / "candidate-request.json"
    if not request_path.exists():
        _atomic_json(
            request_path,
            {
                "schema_version": "asr-retranscription-candidate-v1",
                "candidate_id": row["id"],
                "job_id": row["job_id"],
                "source_revision": int(row["source_revision"]),
                "chunk_index": int(row["chunk_index"]),
                "source_chunk_sha256": row["source_chunk_sha256"],
                "recognizer": row["recognizer"],
                "recognizer_config_sha256": row["recognizer_config_sha256"],
                "language_code": row["language_code"],
                "processing_strategy": row["processing_strategy"],
                "estimated_cost_usd": row["estimated_cost_usd"],
                "confirmed_cost_usd": row["confirmed_cost_usd"],
                "pricing_version": row["pricing_version"],
                "accepted_artifacts_mutated": False,
            },
        )
    return job_dir, candidate


def _env(data_dir: Path, row: dict[str, Any], start_ms: int, end_ms: int) -> dict[str, str]:
    values = os.environ.copy()
    values.update(
        {
            "COURSE_TRANSCRIPT_DATA_DIR": str(data_dir),
            "JOB_NAME": f"{row['job_id']}/{row['candidate_relpath']}",
            "CHUNK_INDEX": str(int(row["chunk_index"])),
            "CHUNK_START_SECONDS": str(start_ms / 1000),
            "CHUNK_END_SECONDS": str(end_ms / 1000),
            "CHUNK_ROLE": "base",
            "CHUNK_PATCH_MODE": "retranscription_candidate",
            "CHIRP_DYNAMIC_BATCHING": (
                "true" if str(row["processing_strategy"]) == DYNAMIC_BATCHING else "false"
            ),
            "LANGUAGE_CODE": str(row["language_code"]),
        }
    )
    return values


def _run_module(module: str, env: dict[str, str], timeout_seconds: int = 900) -> int:
    result = subprocess.run(
        [sys.executable, "-m", module],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
    )
    if result.stdout:
        print(result.stdout.strip())
    return int(result.returncode or 0)


def _still_current(store: JobStore, data_dir: Path, row: dict[str, Any]) -> tuple[bool, str]:
    job = store.get_job(str(row["job_id"]))
    if int(job["revision"]) != int(row["source_revision"]):
        return False, "source job revision changed after candidate approval"
    job_dir = Path(data_dir) / "jobs" / str(row["job_id"])
    current = chunk_source_sha256(job_dir, int(row["chunk_index"]))
    if current != str(row["source_chunk_sha256"]):
        return False, "accepted chunk evidence changed after candidate approval"
    return True, ""


def _mark_stale_if_needed(
    store: JobStore,
    candidates: RetranscriptionCandidateStore,
    data_dir: Path,
    row: dict[str, Any],
    worker_id: str,
) -> bool:
    current, reason = _still_current(store, data_dir, row)
    if current:
        return False
    candidates.mark_stale(str(row["id"]), worker_id, reason=reason)
    return True


def _candidate_manifest(candidate_dir: Path, chunk_index: int) -> dict[str, Any]:
    payload = _read_json(
        candidate_dir / "chunks" / f"chunk-{chunk_index:03d}" / "manifest.json", {}
    )
    return payload if isinstance(payload, dict) else {}


def _submit(
    store: JobStore,
    candidates: RetranscriptionCandidateStore,
    data_dir: Path,
    row: dict[str, Any],
    worker_id: str,
) -> None:
    if _mark_stale_if_needed(store, candidates, data_dir, row, worker_id):
        return
    job_dir, candidate_dir = _prepare_candidate_root(data_dir, row)
    start_ms, end_ms = _chunk_window(job_dir, int(row["chunk_index"]))
    env = _env(data_dir, row, start_ms, end_ms)
    manifest = _candidate_manifest(candidate_dir, int(row["chunk_index"]))

    # Crash recovery: the provider operation may already have been submitted
    # before the DB status was advanced. The hardened submitter retains it.
    if str(manifest.get("status") or "") not in {
        "SUBMITTED", "RUNNING", "RECOVERING", "SUCCEEDED", "EMPTY_SILENCE"
    }:
        code = _run_module("app.providers.chirp_chunk_hardened", env)
        if code != 0:
            candidates.mark_failed(
                str(row["id"]), worker_id,
                kind="submit_failed",
                safe_message=f"Chirp candidate submit failed with exit {code}",
            )
            return
        manifest = _candidate_manifest(candidate_dir, int(row["chunk_index"]))

    status = str(manifest.get("status") or "")
    if status in {"SUCCEEDED", "EMPTY_SILENCE"}:
        _complete(store, candidates, data_dir, row, worker_id)
        return
    operation_name = str(manifest.get("operation_name") or "")
    if not operation_name:
        candidates.mark_failed(
            str(row["id"]), worker_id,
            kind="submission_evidence_missing",
            safe_message="Chirp candidate submit completed without durable operation evidence",
        )
        return
    audio = candidate_dir / "chunks" / f"chunk-{int(row['chunk_index']):03d}" / "audio.flac"
    audio_sha = _sha256_file(audio) if audio.is_file() else ""
    candidates.mark_submitted(
        str(row["id"]), worker_id,
        operation_name=operation_name,
        source_audio_sha256=audio_sha,
    )


def _complete(
    store: JobStore,
    candidates: RetranscriptionCandidateStore,
    data_dir: Path,
    row: dict[str, Any],
    worker_id: str,
) -> None:
    if _mark_stale_if_needed(store, candidates, data_dir, row, worker_id):
        return
    job_dir = Path(data_dir) / "jobs" / str(row["job_id"])
    candidate_dir = _candidate_dir(data_dir, row)
    chunk = candidate_dir / "chunks" / f"chunk-{int(row['chunk_index']):03d}"
    if not (chunk / "words.json").is_file() or not (chunk / "partial-transcript.json").is_file():
        candidates.mark_failed(
            str(row["id"]), worker_id,
            kind="candidate_artifact_missing",
            safe_message="Recovered Chirp candidate is missing local words/transcript evidence",
        )
        return
    current_sha = chunk_source_sha256(job_dir, int(row["chunk_index"]))
    write_candidate_comparison(
        job_dir=job_dir,
        candidate_relpath=str(row["candidate_relpath"]),
        chunk_index=int(row["chunk_index"]),
        source_chunk_sha256=str(row["source_chunk_sha256"]),
        current_source_chunk_sha256=current_sha,
    )
    candidates.mark_completed(str(row["id"]), worker_id)


def _recover(
    store: JobStore,
    candidates: RetranscriptionCandidateStore,
    data_dir: Path,
    row: dict[str, Any],
    worker_id: str,
) -> None:
    if _mark_stale_if_needed(store, candidates, data_dir, row, worker_id):
        return
    job_dir, candidate_dir = _prepare_candidate_root(data_dir, row)
    start_ms, end_ms = _chunk_window(job_dir, int(row["chunk_index"]))
    env = _env(data_dir, row, start_ms, end_ms)
    env["ALLOW_PENDING"] = "1"
    code = _run_module("app.providers.recover_chunk_hardened", env)
    if code == 0:
        _complete(store, candidates, data_dir, row, worker_id)
        return
    if code in {_PENDING_EXIT, _RETRYABLE_EXIT}:
        candidates.mark_processing(str(row["id"]), worker_id)
        return
    candidates.mark_failed(
        str(row["id"]), worker_id,
        kind="recovery_failed",
        safe_message=f"Chirp candidate recovery failed with exit {code}",
    )


def run_once(store: JobStore, *, data_dir: Path, worker_id: str) -> bool:
    candidates = RetranscriptionCandidateStore(store)
    row = candidates.acquire_next(worker_id, lease_seconds=600)
    if row is None:
        return False
    try:
        if str(row["status"]) == "queued":
            _submit(store, candidates, data_dir, row, worker_id)
        elif str(row["status"]) in {"submitted", "processing"}:
            _recover(store, candidates, data_dir, row, worker_id)
        else:
            candidates.mark_failed(
                str(row["id"]), worker_id,
                kind="invalid_state",
                safe_message=f"Unsupported candidate state: {row['status']}",
            )
    except JobConflict:
        raise
    except Exception as exc:
        try:
            candidates.mark_failed(
                str(row["id"]), worker_id,
                kind=type(exc).__name__,
                safe_message="Retranscription candidate worker failed before safe completion",
            )
        except Exception:
            pass
        print(f"RETRANSCRIPTION=FAIL candidate={row['id']} type={type(exc).__name__}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    data_dir = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
    store = JobStore(data_dir / "course-transcript.db")
    worker_id = os.environ.get(
        "COURSE_TRANSCRIPT_RETRANSCRIPTION_WORKER_ID", "retranscription-worker-1"
    )
    if args.once:
        run_once(store, data_dir=data_dir, worker_id=worker_id)
        return 0
    while True:
        worked = run_once(store, data_dir=data_dir, worker_id=worker_id)
        time.sleep(0.5 if worked else max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
