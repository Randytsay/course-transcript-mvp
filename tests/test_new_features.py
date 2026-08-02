from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_output_defaults_and_deprecated_formats():
    from app.jobs.exports import normalize_output_formats

    assert normalize_output_formats(None) == ["srt", "txt"]
    assert normalize_output_formats(["srt", "txt", "csv"]) == ["srt", "txt"]
    assert normalize_output_formats(["srt", "docx", "pdf", "txt"]) == ["srt", "txt"]
    assert normalize_output_formats(["csv"]) == ["csv"]


def test_dynamic_chunk_plan_uses_uniform_first_chunk(monkeypatch):
    monkeypatch.setenv("CHIRP_DYNAMIC_BATCHING", "true")
    module_name = "app.providers.run_chirp_pipeline"
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    plan = module.compute_chunk_plan(2_000)
    assert plan[0] == (0, 0.0, 900.0)
    assert plan[1][1] == 890.0


def test_safe_drive_publish_backs_up_existing_file(tmp_path):
    from app.jobs.drive_publish import publish_outputs

    job = tmp_path / "job-123"
    job.mkdir()
    local = job / "subtitles-corrected.srt"
    local.write_text("new subtitle", encoding="utf-8")
    remote: dict[str, bytes] = {"gdrive:course/lesson.srt": b"old subtitle"}

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        operation = command[1]
        if operation == "size":
            path = command[-1]
            if path not in remote:
                return subprocess.CompletedProcess(command, 1, "", "object not found")
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"count": 1, "bytes": len(remote[path])}),
                "",
            )
        if operation == "copyto":
            source, destination = command[-2], command[-1]
            remote[destination] = Path(source).read_bytes()
            return subprocess.CompletedProcess(command, 0, "", "")
        if operation == "moveto":
            source, destination = command[-2], command[-1]
            if source not in remote:
                return subprocess.CompletedProcess(command, 1, "", "object not found")
            remote[destination] = remote.pop(source)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    state = publish_outputs(
        job,
        source_name="lesson.mp3",
        destination="gdrive:course",
        output_formats=["srt"],
        authorized=True,
        runner=runner,
        sleeper=lambda _: None,
        jitter=lambda: 0,
        clock=lambda: 100,
    )
    assert state["status"] == "completed"
    assert state["backup_count"] == 1
    assert remote["gdrive:course/lesson.srt"] == b"new subtitle"
    backup = state["files"]["srt"]["backup_remote_path"]
    assert remote[backup] == b"old subtitle"
