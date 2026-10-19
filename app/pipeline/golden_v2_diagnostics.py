"""Structured diagnostics for Golden V2 reconciliation conflicts.

This module produces a small, deterministic error record that is safe to log
or persist alongside the existing immutable evidence. It NEVER widens
authority, NEVER alters Ledger CAS, and NEVER touches Provider Raw evidence.

Every V2Conflict raise in the no-dispatch recovery path or in the paid
executor may carry one of these records so the operator can pinpoint the
specific function, condition, and immutable artefact involved.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Stable error codes (canonical names; not used for substring matching).
# Use _SEARCH_TERMS below for the reason->code mapping.
ERROR_CODE = {
    "canonical": "GVR-ND-000",
}
_BY_CODE = {"GVR-ND-000": "canonical"}

# Each entry maps a substring that appears in the human reason to the
# stable error code. The first matching key wins. Keys are sorted by
# specificity (most specific first) so the order of insertion matters.
_SEARCH_TERMS = (
    ("primary audit identity differs from no-dispatch evidence", "GVR-ND-027"),
    ("archived primary attempt identity mismatch", "GVR-ND-030"),
    ("saved no-dispatch proof prior attempt identity mismatch", "GVR-ND-038"),
    ("saved no-dispatch proof raw attempt is malformed", "GVR-ND-036"),
    ("saved no-dispatch proof raw attempt has no identity", "GVR-ND-037"),
    ("saved no-dispatch proof primary audit path is invalid", "GVR-ND-033"),
    ("saved no-dispatch proof lacks its primary audit binding", "GVR-ND-032"),
    ("saved no-dispatch proof references changed raw primary evidence", "GVR-ND-035"),
    ("expected exactly one active or archived primary attempt", "GVR-ND-034"),
    ("expected exactly one source-bound unknown primary audit", "GVR-ND-049"),
    ("no-dispatch evidence scope or schema mismatch", "GVR-ND-001"),
    ("Cloud Monitoring visibility window is not complete", "GVR-ND-002"),
    ("incomplete Cloud Monitoring service coverage", "GVR-ND-003"),
    ("Cloud Monitoring query parameters mismatch", "GVR-ND-004"),
    ("Cloud Monitoring response is incomplete", "GVR-ND-005"),
    ("Cloud Monitoring response was not fully paged", "GVR-ND-006"),
    ("invalid Cloud Monitoring time series", "GVR-ND-007"),
    ("Cloud Monitoring resource identity mismatch", "GVR-ND-008"),
    ("unrecognized API method in the paid-call window", "GVR-ND-009"),
    ("invalid Cloud Monitoring point list", "GVR-ND-010"),
    ("Cloud Monitoring point has no end time", "GVR-ND-011"),
    ("Cloud Monitoring request count is invalid", "GVR-ND-012"),
    ("no positive Vertex request point to validate metric coverage", "GVR-ND-013"),
    ("symlinked no-dispatch evidence path", "GVR-ND-014"),
    ("content-addressed evidence path already contains different data", "GVR-ND-015"),
    ("concurrent content-addressed evidence collision", "GVR-ND-016"),
    ("no-dispatch evidence file unavailable", "GVR-ND-017"),
    ("no-dispatch evidence checksum mismatch", "GVR-ND-018"),
    ("malformed no-dispatch evidence", "GVR-ND-019"),
    ("no-dispatch evidence is not bound to this task", "GVR-ND-020"),
    ("no-dispatch evidence lacks the prior attempt binding", "GVR-ND-021"),
    ("invalid primary audit path", "GVR-ND-022"),
    ("symlinked primary reconciliation archive", "GVR-ND-023"),
    ("unknown primary audit changed since evidence capture", "GVR-ND-024"),
    ("unknown primary audit is malformed", "GVR-ND-025"),
    ("primary audit is not the proven unknown attempt", "GVR-ND-026"),
    ("primary attempt archive already exists", "GVR-ND-028"),
    ("archived unknown primary audit checksum mismatch", "GVR-ND-029"),
    ("unknown primary audit was neither active nor safely archived", "GVR-ND-031"),
    ("primary audit directory unavailable", "GVR-ND-043"),
    ("job is not an eligible zero-usage unknown primary attempt", "GVR-ND-040"),
    ("immutable primary ambiguity evidence checksum mismatch", "GVR-ND-041"),
    ("primary ambiguity artifact does not match the unknown first window", "GVR-ND-042"),
    ("isolated V2 job artifact directory unavailable", "GVR-ND-051"),
    ("fresh no-dispatch evidence revalidation is missing", "GVR-ND-044"),
    ("fresh no-dispatch revalidation checksum mismatch", "GVR-ND-045"),
    ("fresh metrics are not bound to the original evidence", "GVR-ND-046"),
    ("raw primary attempt was not archived exactly once", "GVR-ND-047"),
    ("unknown primary audit filename does not match immutable identity", "GVR-ND-048"),
    ("unknown primary audit model identity mismatch", "GVR-ND-050"),
    ("source artifact directory unavailable", "GVR-EX-100"),
    ("invalid recovery service contract", "GVR-EX-101"),
    ("invalid paid service contract", "GVR-EX-102"),
)

# Fields that MUST NEVER appear in a diagnostic record. If a future V2Conflict
# raise would carry any of these field names, the record is rejected.
REDACTED_FIELDS = frozenset({
    # Field names that are NEVER allowed in a diagnostic record.
    "password", "passwd", "pwd",
    "secret", "client_secret", "shared_secret",
    "token", "access_token", "refresh_token", "id_token", "bearer_token",
    "api_key", "apikey", "api-key",
    "private_key", "priv_key",
    "authorization", "auth", "x-api-key", "x-auth-token",
    "session_id", "cookie", "set-cookie",
    "aws_secret_access_key", "aws_access_key_id",
    "gcp_service_account_key", "google_application_credentials",
    "openai_api_key", "anthropic_api_key", "gemini_api_key",
    "bearer", "x-goog-api-key",
})

# Regex patterns for redacting secrets embedded INSIDE string values
# (e.g. a reason that quotes a request header). The patterns match the
# canonical ``key: value`` and ``key=value`` shapes used by HTTP headers
# and env-var dumps.
_INLINE_SECRET_PATTERNS = (
    # Authorization: Bearer <token>
    (re.compile(r"(authorization\s*[:=]\s*)(?:bearer\s+)?[A-Za-z0-9._\-+/=]{6,}"),
     r'\1[REDACTED]'),
    # password=<value> or password: <value>
    (re.compile(r'(password\s*[:=]\s*)[^\s,"\'\}]+', re.IGNORECASE),
     r'\1[REDACTED]'),
    # token=<value> or api_key=<value>
    (re.compile(r"((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key)\s*[:=]\s*)[A-Za-z0-9._\-+/=]{6,}",
     re.IGNORECASE),
     r'\1[REDACTED]'),
    # Bearer <token> standalone
    (re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-+/=]{6,}"),
     r"Bearer [REDACTED]"),
)


def _redact(value):
    """Recursively strip known sensitive fields, then scrub embedded secrets.

    Step 1: drop entire dict keys that are known to be sensitive
            (REDACTED_FIELDS).
    Step 2: on every string value, run a set of regex patterns to catch
            secrets that appear as ``key: value`` or ``key=value``
            inside a longer free-form text (e.g. a logged HTTP header,
            an env-var dump, a request URL with embedded credentials).
    Step 3: truncate strings longer than 256 chars.
    """
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items() if k not in REDACTED_FIELDS}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        scrubbed = value
        for pattern, replacement in _INLINE_SECRET_PATTERNS:
            scrubbed = pattern.sub(replacement, scrubbed)
        if len(scrubbed) > 256:
            return scrubbed[:128] + "...<truncated>..." + scrubbed[-32:]
        return scrubbed
    return value


def make_record(*, error_code, reason, stage, source, context=None):
    """Build a stable, JSON-serialisable diagnostic record.

    The reason and context are both passed through ``_redact`` to strip
    any sensitive tokens (passwords, API keys, Bearer tokens, refresh
    tokens, etc.) before the record is returned. Records are still safe
    to log or persist.
    """
    _known = {code for _, code in _SEARCH_TERMS} | {"GVR-ND-000", "GVR-EX-199"}
    if error_code not in _known:
        raise ValueError(f"unknown V2 error code: {error_code}")
    file, line = source
    # Redact both the reason string and the context dict so that secrets
    # embedded in the human-readable reason are also scrubbed.
    redacted_reason = _redact(reason) if isinstance(reason, str) else reason
    record = {
        "error_code": error_code,
        "reason": redacted_reason,
        "stage": stage,
        "file": file,
        "line": line,
        "context": _redact(context or {}),
    }
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    record["trace_id"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return record


def error_code_for(reason):
    """Map a V2Conflict reason string to its stable error code.

    Substring search across the table, most-specific match wins. If no
    substring matches, the synthetic GVR-ND-000 code is returned so the
    operator can still see a non-empty code in the diagnostic.
    """
    if not isinstance(reason, str):
        return "GVR-ND-000"
    for substring, code in _SEARCH_TERMS:
        if substring in reason:
            return code
    return "GVR-ND-000"


def context_for_archive(*, identity_sha, expected_identity_sha,
                        prior_attempt_relative, archive_path, archive_present):
    """Build the context dict for the most common no-dispatch failure."""
    def _short(s):
        return s[:16] + "..." if isinstance(s, str) and len(s) > 16 else s
    return {
        "identity_sha": _short(identity_sha),
        "expected_identity_sha": _short(expected_identity_sha),
        "prior_attempt_relative": prior_attempt_relative,
        "archive_path": str(archive_path) if archive_path is not None else None,
        "archive_present": archive_present,
    }
