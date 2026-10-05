from __future__ import annotations

import os
from pathlib import Path

from app.pipeline.dynamic_worker_production import _load_active_runtime_env


def test_load_active_runtime_env_fills_only_missing_allowed_keys(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "ai-active.env").write_text(
        "# active profile\n"
        "GOOGLE_CLOUD_PROJECT=project-from-runtime\n"
        "GOOGLE_CLOUD_LOCATION=us\n"
        "GCS_BUCKET=bucket-from-runtime\n"
        "UNRELATED_SECRET=must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "explicit-location")
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("UNRELATED_SECRET", raising=False)

    loaded = _load_active_runtime_env(runtime)

    assert loaded == ("GOOGLE_CLOUD_PROJECT", "GCS_BUCKET")
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "project-from-runtime"
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "explicit-location"
    assert os.environ["GCS_BUCKET"] == "bucket-from-runtime"
    assert "UNRELATED_SECRET" not in os.environ


def test_load_active_runtime_env_missing_file_is_noop(tmp_path: Path, monkeypatch) -> None:
    for key in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GCS_BUCKET"):
        monkeypatch.delenv(key, raising=False)

    assert _load_active_runtime_env(tmp_path / "missing") == ()
