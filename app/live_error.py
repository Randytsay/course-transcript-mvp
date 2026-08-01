"""Sanitize provider diagnostics before returning them to browsers."""
from __future__ import annotations

import re

_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|token|credential|private[_ -]?key)\s*[:=]\s*\S+"
)
_GCS_PATTERN = re.compile(r"gs://[^\s]+")
_OPERATION_PATTERN = re.compile(r"projects/[^\s]+/operations/[^\s]+")
_PATH_PATTERN = re.compile(r"(?:/[^\s:]+){2,}")


def safe_chunk_error(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        code = value.get("code")
        message = value.get("message") or value.get("detail") or "Chunk failed"
        text = f"{code}: {message}" if code not in {None, ""} else str(message)
    else:
        text = str(value)
    text = _SECRET_PATTERN.sub(r"\1=[REDACTED]", text)
    text = _GCS_PATTERN.sub("gs://[REDACTED]", text)
    text = _OPERATION_PATTERN.sub("operation/[REDACTED]", text)
    text = _PATH_PATTERN.sub("/[REDACTED]", text)
    return text[-300:]
