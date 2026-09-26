from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from app.pipeline import dynamic_worker_hardened as dynamic
from app.pipeline import worker as base
from app.pipeline import worker_observed as observed


class FakeStore:
    def __init__(self, status: str = "processing") -> None:
        self.status = status

    def get_job(self, job_id: str) -> dict[str, str]:
        return {"id": job_id, "status": self.status}

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int,
    ) -> dict[str, str]:
        return {"id": job_id, "status": self.status}


def _large_output_command() -> list[str]:
    return [
        sys.executable,
        "-c",
        (
            "import sys;"
            "sys.stdout.write('completed\\n');"
            "sys.stdout.flush();"
            "sys.stderr.write('x' * 2_000_000);"
            "sys.stderr.flush()"
        ),
    ]


def test_observed_runner_does_not_deadlock_on_large_stderr() -> None:
    started = time.monotonic()
    stdout = observed._run_with_heartbeat(
        _large_output_command(),
        store=FakeStore(),
        job_id="job-1",
        worker_id="worker-1",
        timeout_seconds=5,
    )
    assert stdout == "completed"
    assert time.monotonic() - started < 5


def test_dynamic_pending_runner_does_not_deadlock_on_large_stderr() -> None:
    started = time.monotonic()
    returncode, stdout, stderr = dynamic._run_allow_pending(
        _large_output_command(),
        store=FakeStore(),
        job_id="job-1",
        worker_id="worker-1",
        timeout_seconds=5,
        env=dict(os.environ),
    )
    assert returncode == 0
    assert stdout == "completed"
    assert len(stderr) == 2_000_000
    assert time.monotonic() - started < 5


def test_normalize_progress_watchdog_terminates_stalled_process_group(
    tmp_path: Path,
) -> None:
    progress = tmp_path / "normalized.tmp.flac"
    pid_file = tmp_path / "child.pid"
    command = [
        sys.executable,
        "-c",
        (
            "import os,time,pathlib;"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
            f"pathlib.Path({str(progress)!r}).write_bytes(b'partial');"
            "time.sleep(30)"
        ),
    ]

    with pytest.raises(base.PipelineError, match="長時間無進展"):
        observed._run_with_heartbeat(
            command,
            store=FakeStore(),
            job_id="job-1",
            worker_id="worker-1",
            timeout_seconds=10,
            progress_path=progress,
            stall_timeout_seconds=1,
        )

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
