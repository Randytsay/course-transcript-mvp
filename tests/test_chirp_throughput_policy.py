from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.pipeline import recovery_schedule
from app.pipeline.worker import _chirp_parallelism_for_duration, _module_env
from app.providers.targeted_patch import _env as targeted_patch_env


def test_duration_parallelism_policy_caps_at_eight() -> None:
    assert _chirp_parallelism_for_duration(20 * 60) == 3
    assert _chirp_parallelism_for_duration(30 * 60) == 3
    assert _chirp_parallelism_for_duration(30 * 60 + 1) == 5
    assert _chirp_parallelism_for_duration(90 * 60) == 5
    assert _chirp_parallelism_for_duration(90 * 60 + 1) == 8
    assert _chirp_parallelism_for_duration(5 * 60 * 60) == 8


def test_module_env_uses_duration_policy_and_limit(tmp_path: Path) -> None:
    record = {
        "id": "job-1",
        "source_name": "source.mp3",
        "language_code": "cmn-Hant-TW",
        "enable_gemini_correction": False,
        "content_mode": "general",
        "document_context": "",
        "workflow_mode": "CHIRP_ONLY",
        "chirp_max_parallel_chunks": 3,
    }
    with patch("app.pipeline.worker._normalized_duration_seconds", return_value=2 * 60 * 60), patch.dict(
        os.environ, {"CHIRP_MAX_PARALLEL_CHUNKS_LIMIT": "8"}
    ):
        env = _module_env(record, tmp_path)
    assert env["CHIRP_MAX_PARALLEL_CHUNKS"] == "8"

    with patch("app.pipeline.worker._normalized_duration_seconds", return_value=2 * 60 * 60), patch.dict(
        os.environ, {"CHIRP_MAX_PARALLEL_CHUNKS_LIMIT": "6"}
    ):
        env = _module_env(record, tmp_path)
    assert env["CHIRP_MAX_PARALLEL_CHUNKS"] == "6"


def test_targeted_patch_is_latency_sensitive_standard_batch() -> None:
    env = targeted_patch_env({
        "patch_index": 900700,
        "source_start_ms": 1000,
        "source_end_ms": 61000,
    })
    assert env["CHIRP_DYNAMIC_BATCHING"] == "false"


def test_recovery_schedule_defaults_to_sixty_seconds(tmp_path: Path) -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CHIRP_RECOVERY_POLL_SECONDS", None)
        state = recovery_schedule.schedule(tmp_path, "pending", now=now)
    assert state["next_recovery_at"] == "2026-09-27T00:01:00+00:00"
