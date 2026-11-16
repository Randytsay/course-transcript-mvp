"""Owner-only, fail-closed dashboard policy for a single Golden V2 Drive batch.

No provider calls, no new owner credentials, no automatic grants.
The authorization endpoint retains the existing HMAC-signed immutable grant.
All money values are integer US cents, and budget exposure is conservative.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.jobs import drive_api

MIN_DURATION_SECONDS = 3600
MAX_BATCH_CENTS = 10000
MAX_UI_JOBS = 500


def scope_prefix() -> str | None:
    """A verified, operator-set Drive source prefix; absent means no UI/policy."""
    if os.environ.get("COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED") != "true":
        return None
    value = os.environ.get("COURSE_TRANSCRIPT_V2_PAID_CONSOLE_SCOPE_PREFIX", "")
    if (not value.startswith("gdrive:") or not value.endswith("/")
            or len(value) > 1024 or "//" in value or "\x00" in value
            or any(segment in (".", "..") for segment in value[7:].split("/"))):
        raise V2Conflict("Golden V2 paid console scope is not configured")
    return value


@contextmanager
def owner_batch_lock(root: Path):
    """Serialize new-console and legacy authorize-paid grants for the scope."""
    fd = os.open(root / "owner-paid-console.lock",
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _connection(database: Path):
    uri = f"{Path(database).resolve().as_uri()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _records(db: sqlite3.Connection, prefix: str) -> list[dict]:
    """Only intake-verified exact source paths in this one owner batch."""
    records = []
    for row in db.execute("SELECT source,state,result FROM v2_quotes"):
        try:
            source = json.loads(row["source"])
            path = source["source_path"]
            if not isinstance(path, str) or not path.startswith(prefix):
                continue
            result = json.loads(row["result"]) if row["result"] else None
            # An MP3 quote is not transferable to a different Drive file.
            if (not path.lower().endswith(".mp3") or
                    not isinstance(source.get("file_id"), str) or
                    not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", source["file_id"])):
                raise V2Conflict("batch source identity invalid")
            if result and (result.get("drive_source") != source
                           or not isinstance(result.get("job_id"), str)
                           or not re.fullmatch(r"v2p_[0-9a-f]{32}", result["job_id"])):
                raise V2Conflict("batch quote/source mismatch")
            records.append({"source": source, "state": row["state"], "result": result})
        except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise V2Conflict("batch quote evidence invalid") from exc
    if len(records) > MAX_UI_JOBS:
        raise V2Conflict("batch exceeds paid-console bounded inventory")
    return records


def _exposure(db: sqlite3.Connection, job_ids: set[str]) -> int:
    """Reserve the larger of each approved cap and recorded stage exposure.

    Never release money based on unverified provider invoices; a completed
    stage's local actual may be lower than its original approved ceiling.
    """
    total = 0
    for job_id in job_ids:
        row = db.execute("SELECT approved_cents FROM v2_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["approved_cents"] is None:
            continue
        stage_exposure = 0
        for stage in db.execute(
            "SELECT state,estimate_cents,actual_cents FROM v2_stages WHERE job_id=?", (job_id,)
        ):
            actual = stage["actual_cents"]
            if stage["state"] in ("COMPLETE", "SKIPPED") and actual is not None:
                stage_exposure += int(actual)
            else:
                stage_exposure += max(int(stage["estimate_cents"]), int(actual or 0))
        total += max(int(row["approved_cents"]), stage_exposure)
    return total


def snapshot(database: Path, prefix: str) -> dict:
    """Read-only, secrets-free view for the existing authenticated admin UI."""
    with closing(_connection(database)) as db:
        records = _records(db, prefix)
        known_job_ids = {r["result"]["job_id"] for r in records if r["result"]}
        exposure = _exposure(db, known_job_ids)
        summary = {"eligible": 0, "excluded_short": 0, "pending_quote": 0,
                   "awaiting_approval": 0, "approved_or_active": 0, "failed_quote": 0}
        jobs = []
        for entry in records:
            result, source, state = entry["result"], entry["source"], entry["state"]
            if state != "COMPLETE" or result is None:
                key = "failed_quote" if state in ("FAILED", "OVER_CAP") else "pending_quote"
                summary[key] += 1
                continue
            seconds = result.get("duration_seconds")
            if type(seconds) not in (int, float) or not 0 < seconds < 10**9:
                raise V2Conflict("trusted quote duration is invalid")
            job = db.execute(
                "SELECT id,status,revision,approved_cents,estimated_cents,source_sha "
                "FROM v2_jobs WHERE id=?", (result["job_id"],)
            ).fetchone()
            if (job is None or job["source_sha"] != result.get("source_sha256")
                    or int(job["estimated_cents"]) != int(result["estimated_cents"])):
                raise V2Conflict("trusted quote/ledger binding mismatch")
            eligible = seconds >= MIN_DURATION_SECONDS
            summary["eligible" if eligible else "excluded_short"] += 1
            if job["approved_cents"] is not None:
                summary["approved_or_active"] += 1
            elif eligible and job["status"] == "AWAITING_APPROVAL":
                summary["awaiting_approval"] += 1
            jobs.append({
                "job_id": job["id"],
                "name": source["name"],
                "duration_seconds": seconds,
                "estimated_cents": int(result["estimated_cents"]),
                "quote_sha256": result.get("quote_sha256"),
                "source_sha256": result["source_sha256"],
                "status": job["status"],
                "revision": job["revision"],
                "approved_cents": job["approved_cents"],
                "eligible": eligible,
                "can_approve": bool(eligible and job["status"] == "AWAITING_APPROVAL"
                                    and job["approved_cents"] is None),
            })
    jobs.sort(key=lambda j: (not j["can_approve"], j["estimated_cents"], j["job_id"]))
    return {"schema": "golden-v2-paid-console-v1",
            "min_duration_seconds": MIN_DURATION_SECONDS,
            "batch_cap_cents": MAX_BATCH_CENTS,
            "reserved_exposure_cents": exposure,
            "remaining_cents": max(0, MAX_BATCH_CENTS - exposure),
            "scope_prefix": prefix, "summary": summary, "jobs": jobs,
            "invoice_verified": False,
            "note": "Local conservative reserve, not provider's final invoice."}


def matching_quote(database: Path, prefix: str, job_id: str):
    with closing(_connection(database)) as db:
        return next((r for r in _records(db, prefix)
                     if r["result"] and r["result"]["job_id"] == job_id), None)


def check_paid_approval(database: Path, prefix: str, job_id: str,
                        approved_cents: int, source_sha256: str, quote_sha256: str) -> dict:
    """Under owner_batch_lock, enforce immutable source/duration/cumulative cost."""
    with closing(_connection(database)) as db:
        scoped = _records(db, prefix)
        entry = next((r for r in scoped if r["result"]
                      and r["result"]["job_id"] == job_id), None)
        if not entry:
            raise V2Conflict("job is not an eligible member of this configured batch")
        if entry["state"] != "COMPLETE":
            raise V2Conflict("trusted source duration quote is incomplete")
        source, quote = entry["source"], entry["result"]
        seconds = quote.get("duration_seconds")
        if type(seconds) not in (int, float) or seconds < MIN_DURATION_SECONDS:
            raise V2Conflict("MP3 duration below 3600 seconds: paid processing forbidden")
        if (source_sha256 != quote.get("source_sha256")
                or quote_sha256 != quote.get("quote_sha256")
                or approved_cents < quote.get("estimated_cents", 10**10)
                or type(approved_cents) is not int):
            raise V2Conflict("owner grant differs from trusted quote")
        # The immutable local source/quote is insufficient if Drive changed.
        observed = drive_api.read_drive_media_metadata(source["source_path"])
        expected = {"id": source["file_id"], "version": source["version"],
                    "name": source["name"], "size_bytes": source["size_bytes"],
                    "md5_checksum": source["md5_checksum"]}
        if any(observed.get(key) != value for key, value in expected.items()):
            raise V2Conflict("Drive source changed since trusted quote; re-quote required")
        all_ids = {r["result"]["job_id"] for r in scoped if r["result"]}
        reserved = _exposure(db, all_ids)
        if reserved + approved_cents > MAX_BATCH_CENTS:
            raise V2Conflict("US$100 cumulative batch ceiling would be exceeded")
        return {"duration_seconds": seconds, "reserved_before_cents": reserved,
                "reserved_after_cents": reserved + approved_cents,
                "batch_cap_cents": MAX_BATCH_CENTS}
