"""Cross-process lock for Drive publication of the same source sidecars."""
from __future__ import annotations

import fcntl
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def drive_publish_lock(data_dir: Path, source_path: str) -> Iterator[None]:
    lock_dir = data_dir / "locks" / "drive-publish"
    lock_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()
    path = lock_dir / f"{digest}.lock"
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
