"""Crash-safe, exact-byte immutable evidence creation for Golden V2.

Never expose a partially written final filename. Replays are idempotent only
when they match every original byte, including the provider's JSON spacing.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict


def store_immutable_bytes(path: Path, content: bytes) -> str:
    if not isinstance(content, bytes):
        raise TypeError("immutable evidence must be bytes")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                         delete=False) as stream:
            tmp_name = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp_name, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise V2Conflict(f"immutable evidence conflict: {path.name}")
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)
    return digest
