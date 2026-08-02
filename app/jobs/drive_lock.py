"""Cross-process global lock for all Google Drive publication writes."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def drive_publish_lock(data_dir: Path, source_path: str) -> Iterator[None]:
    """Serialize Drive mutations across pipeline, delivery, and editor processes.

    `source_path` is retained in the signature for caller clarity and future
    diagnostics. A single global lock is deliberate: rclone's in-process
    throttling cannot coordinate request rates across multiple containers.
    """
    if not source_path:
        raise ValueError("Drive publication source path is required")
    lock_dir = data_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / "drive-publish-global.lock"
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
