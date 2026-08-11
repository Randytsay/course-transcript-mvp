"""Small, dependency-free service heartbeats for production health checks."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def write_service_heartbeat(data_dir: Path, service: str, *, state: str = "running") -> None:
    directory = data_dir / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "service": service,
        "state": state,
        "pid": os.getpid(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, delete=False
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(directory / f"{service}.heartbeat.json")
