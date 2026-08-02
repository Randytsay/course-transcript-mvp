"""Cross-process global lock for all Google Drive publication writes."""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def drive_publish_lock(data_dir: Path, source_path: str) -> Iterator[None]:
    """Serialize Drive mutations and enforce a shared inter-process cooldown.

    `source_path` is retained in the signature for caller clarity and future
    diagnostics. A single global lock is deliberate: rclone's in-process
    throttling cannot coordinate request rates across multiple containers.
    """
    if not source_path:
        raise ValueError("Drive publication source path is required")
    lock_dir = data_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / "drive-publish-global.lock"
    minimum_interval = max(
        0.0,
        float(os.environ.get("DRIVE_GLOBAL_MIN_INTERVAL_SECONDS", "1.0")),
    )
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.seek(0)
            try:
                last_released_at = float(stream.read().strip() or "0")
            except ValueError:
                last_released_at = 0.0
            delay = minimum_interval - (time.time() - last_released_at)
            if delay > 0:
                time.sleep(delay)
            yield
        finally:
            stream.seek(0)
            stream.truncate()
            stream.write(f"{time.time():.6f}")
            stream.flush()
            os.fsync(stream.fileno())
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
