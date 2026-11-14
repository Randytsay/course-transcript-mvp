"""Paid-side auto-rescue hook: invoke the operator rescue script once per
newly eligible job.

Each cycle runs the provider-free worker pass and, for every job whose
read-only eligibility outcome says a verified SAFETY refusal is waiting,
spawns the existing scripts/golden_v2_independent_rescue.py --submit as a
detached process. The hook itself never submits a provider request, never
retries an unknown outcome, and never invents authorization: the spawned
script re-verifies the signed owner grant, live Drive revision, pricing,
budget and revision before claiming, exactly as an operator invocation
would. An immutable launch intent is durably recorded before spawning.
An ambiguous launch is NEVER automatically attempted again; the owner must
reconcile whether a paid request started before authorizing any new attempt.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.jobs.golden_v2_read import list_v2_active_jobs
from app.operations.runtime_heartbeat import write_service_heartbeat
from app.pipeline.golden_v2_auto_rescue import inspect_auto_rescue_eligibility
from app.pipeline.golden_v2_worker import run_v2_worker_pass

AUTO_RESCUE_REFERENCE = "golden-v2-auto-rescue: verified SAFETY refusal"
HOOK_STATE_SCHEMA = "golden-v2-auto-rescue-hook-state-v1"
STATE_FILENAME = "auto-rescue-hook-state.json"
LOG_DIRNAME = "auto-rescue-logs"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_INTERVAL_SECONDS = 3600


def _state_path(artifact_root: Path) -> Path:
    return Path(artifact_root) / STATE_FILENAME


def _load_state(artifact_root: Path) -> dict[str, dict[str, object]]:
    path = _state_path(artifact_root)
    if not path.exists():
        return {}
    if path.is_symlink():
        raise V2Conflict("auto-rescue hook state is a symlink")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V2Conflict("auto-rescue hook state is unreadable") from exc
    if (not isinstance(data, dict) or data.get("schema") != HOOK_STATE_SCHEMA
            or not isinstance(data.get("jobs"), dict)):
        raise V2Conflict("auto-rescue hook state is corrupt")
    return data["jobs"]


def _save_state(artifact_root: Path, jobs: dict[str, dict[str, object]]) -> None:
    path = _state_path(artifact_root)
    payload = {"schema": HOOK_STATE_SCHEMA, "jobs": jobs}
    wire = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(wire)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        # Durability across host power loss requires the renamed directory
        # entry to be fsynced too, not just the temporary file contents.
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def inspect_auto_rescue_candidates(
    *, database: Path, artifact_root: Path, max_jobs: int = 100,
) -> dict[str, object]:
    # A genuinely read-only preview: no worker pass, no heartbeat, no
    # staging, no source downloads, no provider clients and no ledger writes.
    source_root = Path(artifact_root)
    source_database = Path(database)
    if source_root.is_symlink() or source_database.is_symlink():
        raise V2Conflict("auto-rescue preview rejects symlinks")
    root = source_root.resolve()
    database = source_database.resolve()
    if (not 1 <= max_jobs <= 1000
            or database.parent != root or not database.is_file()):
        raise V2Conflict("auto-rescue preview requires an existing isolated ledger")
    ledger = GoldenV2Ledger.open_existing(database)
    jobs = list_v2_active_jobs(
        database, limit=max_jobs, statuses=("NEEDS_REVIEW",),
        newest_first=True)
    outcomes = []
    for job in jobs:
        job_id = job["job_id"]
        job_dir = root / "jobs" / job_id
        if (job_dir.is_symlink() or not job_dir.is_dir()
                or job_dir.resolve().parent != (root / "jobs").resolve()):
            eligibility = {"eligible": False, "reason": "JOB_DIR_INVALID"}
        else:
            eligibility = inspect_auto_rescue_eligibility(
                ledger=ledger, job_id=job_id, job_dir=job_dir)
        outcomes.append({"job_id": job_id, "eligibility": eligibility})
    return {
        "action": "AUTO_RESCUE_READ_ONLY_PREVIEW",
        "jobs_seen": len(jobs), "outcomes": outcomes,
        "provider_calls_started": False,
        "drive_mutation_started": False,
        "ledger_modified": False,
    }


def run_auto_rescue_hook_cycle(
    *, database: Path, artifact_root: Path, rescue_script: Path,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_interval_seconds: int = DEFAULT_RETRY_INTERVAL_SECONDS,
    max_jobs: int = 100, now: datetime | None = None,
) -> dict[str, object]:
    """Run one worker pass, spawning the rescue script for eligible jobs."""
    if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise ValueError("hook attempt bound must be 1..10")
    if (type(retry_interval_seconds) is not int
            or not 0 <= retry_interval_seconds <= 7 * 24 * 3600):
        raise ValueError("hook retry interval must be 0..7 days in seconds")
    if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
        raise ValueError("hook job bound must be 1..1000")
    rescue_script = Path(rescue_script)
    if not rescue_script.is_file() or rescue_script.is_symlink():
        raise V2Conflict("independent rescue script is missing")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("hook cycle time must be timezone-aware")
    database = Path(database)
    artifact_root = Path(artifact_root)
    # Cross-process lock covers the eligibility read, intent persistence and
    # process spawn. Never depend on in-memory or PID-only idempotency.
    lock_path = artifact_root / "auto-rescue-hook.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"action": "AUTO_RESCUE_HOOK_BUSY",
                    "spawned": [], "deferred": []}
        return _run_auto_rescue_hook_cycle_locked(
            database=database, artifact_root=artifact_root,
            rescue_script=rescue_script, max_attempts=max_attempts,
            retry_interval_seconds=retry_interval_seconds,
            max_jobs=max_jobs, moment=moment,
        )


def _run_auto_rescue_hook_cycle_locked(
    *, database: Path, artifact_root: Path, rescue_script: Path,
    max_attempts: int, retry_interval_seconds: int, max_jobs: int,
    moment: datetime,
) -> dict[str, object]:
    result = run_v2_worker_pass(
        database=database, artifact_root=artifact_root,
        worker_id="golden-v2-auto-rescue-hook", max_jobs=max_jobs,
    )
    if result.get("action") != "WORKER_CYCLE_COMPLETE":
        return {"action": "AUTO_RESCUE_HOOK_CYCLE_FAILED",
                "worker_action": result.get("action"),
                "reason": result.get("reason", "worker pass did not complete"),
                "spawned": [], "deferred": []}
    ledger = GoldenV2Ledger.open_existing(database)
    state = _load_state(artifact_root)
    spawned: list[dict[str, object]] = []
    deferred: list[dict[str, object]] = []
    changed = False
    for item in result.get("outcomes", []):
        outcome = item.get("auto_rescue") or {}
        if not outcome.get("eligible"):
            continue
        job_id = item["job_id"]
        record = state.get(job_id) or {"attempts": 0}
        attempts = record.get("attempts", 0)
        if not isinstance(attempts, int) or attempts < 0:
            raise V2Conflict("auto-rescue hook state has an invalid attempt count")
        if attempts >= max_attempts:
            deferred.append({"job_id": job_id, "reason": "ATTEMPTS_EXHAUSTED",
                             "attempts": attempts})
            continue
        # Even an old-format state record means a process may already have
        # dispatched a billable request. A cooldown is not evidence that the
        # previous request did NOT happen. A human must reconcile the grant,
        # ledger and provider before another launch can ever be allowed.
        if attempts:
            deferred.append({"job_id": job_id,
                             "reason": "PRIOR_LAUNCH_RECONCILIATION_REQUIRED",
                             "phase": record.get("phase", "LEGACY_UNKNOWN"),
                             "attempts": attempts})
            continue
        last = record.get("last_attempt_at")
        if isinstance(last, str):
            try:
                last_attempt = datetime.fromisoformat(last)
            except ValueError as exc:
                raise V2Conflict("auto-rescue hook state has an invalid timestamp") from exc
            if (moment - last_attempt).total_seconds() < retry_interval_seconds:
                deferred.append({"job_id": job_id, "reason": "COOLING_OFF",
                                 "attempts": attempts})
                continue
        # Fresh re-verification immediately before spawning: the worker
        # outcome is from this same cycle, but the ledger may have moved.
        job_dir = artifact_root / "jobs" / job_id
        fresh = inspect_auto_rescue_eligibility(
            ledger=ledger, job_id=job_id, job_dir=job_dir)
        if not fresh.get("eligible"):
            deferred.append({"job_id": job_id, "reason": "NO_LONGER_ELIGIBLE",
                             "detail": fresh.get("reason")})
            continue
        status = ledger.status(job_id)
        stamp = moment.strftime("%Y%m%dT%H%M%SZ")
        log_dir = artifact_root / LOG_DIRNAME
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}-{stamp}.log"
        state[job_id] = {
            "attempts": attempts + 1,
            "last_attempt_at": moment.isoformat(),
            "phase": "LAUNCH_INTENT_UNCERTAIN",
            "revision": status["revision"],
            "source_sha256": status["source_sha"],
            "last_log": str(log_path),
        }
        # Durable intent FIRST; on a crash, fail closed instead of spawning a
        # second process whose first Speech POST may already have been billed.
        _save_state(artifact_root, state)
        with log_path.open("xb") as log:
            process = subprocess.Popen(
                [sys.executable, str(rescue_script),
                 "--root", str(artifact_root),
                 "--job-id", job_id,
                 "--source-sha", str(status["source_sha"]),
                 "--revision", str(status["revision"]),
                 "--cap-cents", str(status["approved_cents"]),
                 "--reference", AUTO_RESCUE_REFERENCE,
                 "--submit"],
                stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
        state[job_id]["phase"] = "PROCESS_LAUNCHED_RECONCILE_LEDGER"
        state[job_id]["last_pid"] = process.pid
        _save_state(artifact_root, state)
        changed = True
        spawned.append({"job_id": job_id, "pid": process.pid,
                        "log": str(log_path), "revision": status["revision"],
                        "cap_cents": status["approved_cents"]})
    return {"action": "AUTO_RESCUE_HOOK_CYCLE_COMPLETE",
            "jobs_seen": result.get("jobs_seen"),
            "spawned": spawned, "deferred": deferred}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path,
        default=Path(os.environ.get(
            "COURSE_TRANSCRIPT_V2_LEDGER_PATH", "/app/data/golden-v2/ledger.sqlite",
        )),
    )
    parser.add_argument(
        "--artifact-root", type=Path,
        default=Path(os.environ.get(
            "COURSE_TRANSCRIPT_V2_ARTIFACT_ROOT", "/app/data/golden-v2",
        )),
    )
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--retry-interval-seconds", type=int,
                        default=DEFAULT_RETRY_INTERVAL_SECONDS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="read-only eligibility scan; never spawns")
    parser.add_argument("--enable-auto-rescue", action="store_true",
                        help="opt in to the separately approved paid recovery worker")
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 3600:
        parser.error("--poll-seconds must be between 1 and 3600")
    if not 1 <= args.max_jobs <= 1000:
        parser.error("--max-jobs must be between 1 and 1000")
    if not 1 <= args.max_attempts <= 10:
        parser.error("--max-attempts must be between 1 and 10")
    if not 0 <= args.retry_interval_seconds <= 7 * 24 * 3600:
        parser.error("--retry-interval-seconds must be between 0 and 7 days")
    if args.dry_run:
        result = inspect_auto_rescue_candidates(
            database=args.database, artifact_root=args.artifact_root,
            max_jobs=args.max_jobs)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")),
              flush=True)
        return 0
    if not args.enable_auto_rescue:
        parser.error("auto-rescue is disabled by default; use --dry-run for inspection")
    rescue_script = Path(__file__).resolve().parent / "golden_v2_independent_rescue.py"
    if not rescue_script.is_file():
        parser.error("independent rescue script is missing")

    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    data_dir = args.artifact_root.parent
    service_state = "once" if args.once else "running"
    try:
        while not stop.is_set():
            write_service_heartbeat(data_dir, "golden-v2-auto-rescue-hook",
                                    state=service_state)
            try:
                result = run_auto_rescue_hook_cycle(
                    database=args.database, artifact_root=args.artifact_root,
                    rescue_script=rescue_script, max_attempts=args.max_attempts,
                    retry_interval_seconds=args.retry_interval_seconds,
                    max_jobs=args.max_jobs,
                )
            except Exception as exc:  # keep polling; --once reports the failure
                result = {"action": "AUTO_RESCUE_HOOK_CYCLE_FAILED",
                          "reason": f"{type(exc).__name__}: {exc}",
                          "spawned": [], "deferred": []}
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                  flush=True)
            if args.once:
                return 0 if result["action"] == "AUTO_RESCUE_HOOK_CYCLE_COMPLETE" else 2
            if stop.wait(args.poll_seconds):
                break
    finally:
        write_service_heartbeat(data_dir, "golden-v2-auto-rescue-hook",
                                state="stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
