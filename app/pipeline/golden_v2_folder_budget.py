"""A single user-submitted Drive folder request = at most US$6 quoted paid work.

This creates a verifiable *batch-scope decision*, not an unbounded account
auto-spend setting. The operator must present a fresh link each time. The
existing Owner write token, source-bound quote hashes, provider receipts,
paid-stage gates and Golden QA remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from app.pipeline.golden_v2_pricing import PRICE_VERSION, assert_current

MAX_FOLDER_CENTS = 600
POLICY_ID = "GOLDEN_V2_EXPLICIT_FOLDER_USD6_20261025"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID = re.compile(r"^v2p_[0-9a-f]{32}$")


def _encode(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _hash(obj: dict) -> str:
    return hashlib.sha256(_encode(obj)).hexdigest()


def _text(v, pattern, description: str) -> str:
    if not isinstance(v, str) or not pattern.fullmatch(v):
        raise ValueError(description)
    return v


def validate_folder_decision(
    *, preview: dict, quote_responses: list[dict], requested_link: str,
    max_cents: int = MAX_FOLDER_CENTS, today=None,
) -> dict:
    """Fail closed if one audio/quote is missing or batch total exceeds $6.

    quote_responses are the verbatim trusted owner API quote-status records.
    The decision cannot be broadened to unseen future files or other folders.
    """
    if type(max_cents) is not int or max_cents != MAX_FOLDER_CENTS:
        raise ValueError("hard cap must remain exactly 600 cents")
    assert_current(today)
    if not isinstance(requested_link, str) or not requested_link.startswith("https://"):
        raise ValueError("explicit HTTPS Drive folder URL required")
    parts = urlsplit(requested_link)
    if parts.hostname not in {"drive.google.com", "docs.google.com"} or parts.username or parts.password:
        raise ValueError("only explicit Google Drive URL supported")
    if not isinstance(preview, dict) or preview.get("link") not in (None, requested_link):
        raise ValueError("Drive preview URL drift")
    preview_sha = _text(preview.get("preview_sha256"), _DIGEST, "missing preview SHA")
    files = preview.get("files")
    if not isinstance(files, list) or not files or len(files) > 100:
        raise ValueError("bounded nonempty preview files required")
    by_id = {}
    versions = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("invalid preview entry")
        file_id = item.get("file_id")
        version = item.get("version")
        if not isinstance(file_id,str) or not file_id or file_id in by_id or not version:
            raise ValueError("duplicate or incomplete source identity")
        _text(item.get("md5_checksum"), re.compile(r"^[a-fA-F0-9]{32}$"),
              "missing source MD5")
        if type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0:
            raise ValueError("missing file size")
        source_path=item.get("source_path")
        if not isinstance(source_path,str) or not source_path:
            raise ValueError("missing source path")
        versions.add(str(version))
        by_id[file_id] = item
    if len(quote_responses) != len(files):
        raise ValueError("every discovered file must have its own quote")
    items = []
    seen = set()
    aggregate = 0
    for response in quote_responses:
        if not isinstance(response, dict) or response.get("state") != "COMPLETE":
            raise ValueError("quote is missing, failed or expired")
        quote = response.get("result")
        if not isinstance(quote, dict):
            raise ValueError("trusted result required")
        source = quote.get("drive_source")
        if not isinstance(source,dict):
            raise ValueError("quote is not source-bound")
        file_id = source.get("file_id")
        if file_id not in by_id or file_id in seen:
            raise ValueError("quote source not in this folder or duplicated")
        expected = by_id[file_id]
        if any(source.get(key) != expected.get(key) for key in
               ("file_id","version","size_bytes","md5_checksum","source_path")):
            raise ValueError("quote Drive source version/MD5 changed")
        if quote.get("mode") != "FLASH_CHIRP" or quote.get("price_version") != PRICE_VERSION:
            raise ValueError("untrusted model/price configuration")
        try:
            expiry = date.fromisoformat(quote["valid_through"])
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("unverifiable quote expiry") from exc
        if expiry < (today or datetime.now(timezone.utc).date()):
            raise ValueError("quote expired")
        cents = quote.get("estimated_cents")
        stages=quote.get("estimated_stage_cents")
        if (type(cents) is not int or cents <= 0 or cents > max_cents or
                not isinstance(stages,dict) or
                any(type(v) is not int or v < 0 for v in stages.values()) or
                sum(stages.values()) != cents):
            raise ValueError("untrusted or above-cap price")
        job=_text(quote.get("job_id"),_JOB_ID,"missing source-bound job ID")
        quote_sha=_text(quote.get("quote_sha256"),_DIGEST,"missing quote SHA")
        source_sha=_text(quote.get("source_sha256"),_DIGEST,"missing normalized source SHA")
        aggregate += cents
        seen.add(file_id)
        items.append({
            "file_id":file_id,
            "version":str(source["version"]),
            "md5_checksum":source["md5_checksum"],
            "source_sha256":source_sha,
            "job_id":job,
            "quote_id":response["quote_id"],
            "quote_sha256":quote_sha,
            "quoted_cents":cents,
        })
    if seen != set(by_id):
        raise ValueError("all audio files must be included")
    if aggregate > max_cents:
        raise ValueError("WHOLE_FOLDER_USD6_CAP_EXCEEDED")
    proposal={
        "schema":"golden-v2-explicit-folder-usd6-v1",
        "authorization_scope":"ONLY_THIS_EXPLICIT_USER_FOLDER_REQUEST",
        "policy_id":POLICY_ID,
        "requested_link":requested_link,
        "preview_sha256":preview_sha,
        "max_total_cents":max_cents,
        "quoted_total_cents":aggregate,
        "source_count":len(items),
        "items":sorted(items,key=lambda x:x["file_id"]),
        "golden_pass_claim":False,
        "human_listening_claim":False,
        "publication_granted":False,
        "delivery_label":"AI_REVIEWED_WITH_OPEN_ITEMS_NOT_GOLDEN",
        "provider_submission_started":False,
    }
    proposal["decision_sha256"]=_hash(proposal)
    return proposal


def verified_quote_cap(decision: dict, file_id: str) -> int:
    """Only exact included source can reuse a folder decision; no other jobs."""
    if not isinstance(decision,dict):
        raise ValueError("missing source-bound decision")
    checksum=decision.get("decision_sha256")
    content=dict(decision)
    content.pop("decision_sha256",None)
    if checksum!=_hash(content) or decision.get("max_total_cents")!=600:
        raise ValueError("folder budget decision has changed")
    selected=[x for x in decision.get("items",[]) if x.get("file_id")==file_id]
    if len(selected)!=1 or sum(x["quoted_cents"] for x in decision["items"])>600:
        raise ValueError("file not authorized or folder over cap")
    return selected[0]["quoted_cents"]



def save_immutable_budget_decision(decision: dict, audit_dir: Path) -> Path:
    """Preserve exact one-link scope before any paid grant attempt.

    The protected Owner API remains the authorization authority. This durable
    operator audit is source-bound and idempotent, never a substitute for the
    actual signed per-job ledger grant.
    """
    decision_sha = decision.get("decision_sha256")
    canonical = dict(decision)
    canonical.pop("decision_sha256", None)
    if (not isinstance(decision_sha,str) or decision_sha != _hash(canonical)
            or decision.get("max_total_cents") != MAX_FOLDER_CENTS):
        raise ValueError("tampered or over-cap decision")
    path = Path(audit_dir).expanduser()
    if path.is_symlink():
        raise ValueError("audit directory symlink forbidden")
    if not path.exists():
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = path.stat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or stat.S_IMODE(st.st_mode) & 0o077):
        raise ValueError("owner-only audit directory required")
    dest=path/(decision_sha+".json")
    raw=_encode(decision)+b"\n"
    flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd=os.open(dest,flags,0o600)
    except FileExistsError:
        if dest.is_symlink() or dest.read_bytes()!=raw:
            raise ValueError("existing budget audit differs")
        return dest
    with os.fdopen(fd,"wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return dest
