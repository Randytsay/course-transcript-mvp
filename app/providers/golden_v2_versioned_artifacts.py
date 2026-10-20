"""Immutable versioned Golden V2 artifacts, ledger SHA is the selector.

Historical canonical filenames are never overwritten. When a new version
differs, its full SHA names a sibling artifact. The stage ledger decides
which version is current, not path modification time or glob order.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_immutable import store_immutable_bytes


def version_path(canonical: Path, digest: str) -> Path:
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)):
        raise V2Conflict("invalid versioned artifact SHA")
    canonical = Path(canonical)
    return canonical.with_name(f"{canonical.stem}-{digest}{canonical.suffix}")


def resolve_versioned(canonical: Path, digest: str) -> Path:
    """Locate only exact hash-matching immutable bytes, fail closed."""
    canonical = Path(canonical)
    alternate = version_path(canonical, digest)
    for path in (canonical, alternate):
        if path.is_symlink():
            raise V2Conflict("symlink in immutable V2 artifact")
        if not path.is_file():
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
        if path == alternate:
            raise V2Conflict("missing or changed ledger/source evidence: content-addressed V2 artifact")
    raise V2Conflict(f"ledger/source evidence mismatch: missing or changed ledger-bound V2 artifact: {canonical.name}")


def write_versioned(canonical: Path, content: bytes) -> tuple[str, Path]:
    """Content-addressed append-only output; no canonical rewrite."""
    if not isinstance(content, bytes):
        raise V2Conflict("immutable artifact content must be bytes")
    canonical = Path(canonical)
    digest = hashlib.sha256(content).hexdigest()
    if canonical.is_symlink():
        raise V2Conflict("canonical immutable artifact is symlink")
    selected = canonical if not canonical.exists() else (
        canonical if hashlib.sha256(canonical.read_bytes()).hexdigest() == digest
        else version_path(canonical, digest))
    if selected.is_symlink():
        raise V2Conflict("versioned artifact is symlink")
    store_immutable_bytes(selected, content)
    if hashlib.sha256(selected.read_bytes()).hexdigest() != digest:
        raise V2Conflict("immutable artifact failed post-write SHA")
    return digest, selected
