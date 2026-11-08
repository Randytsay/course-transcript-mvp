"""Owner-attested Drive revision backup, checked before publication side effects.

Only the exact old Drive file id + bytes + version may be renamed. This file
contains no cloud calls or authorization bypass; the caller must hold the
shared Drive lock and verify the signed owner publication grant first.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict


def _sha256(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise V2Conflict("old Drive revision content cannot be read back")
    return hashlib.sha256(data).hexdigest()


def verified_backup_plan(*, gateway: Any, parent: str, outputs: dict,
                         approved: dict, saved_ids: dict[str, str]) -> dict:
    """Complete read-only preflight of every output before the first write.

    Requires exact old identity, bytes, version, safe unique '(舊)' filename;
    also recognizes our own previously-promoted pending file on idempotent
    retry. A human-edited file, competing named file, missing approved old
    revision or unapproved collision always blocks.
    """
    if not isinstance(approved, dict) or set(approved) - set(outputs):
        raise V2Conflict("owner backup grant contains unrequested file")
    ids: set[str] = set()
    backups: set[str] = set()
    plan: dict = {}
    for name in outputs:
        if "/" in name or "\\" in name or not name:
            raise V2Conflict("unsafe destination name")
        approved_old = approved.get(name)
        matches = gateway.named(parent, name)
        if len(matches) > 2:
            raise V2Conflict("ambiguous Drive destination revisions")
        own_id = saved_ids.get(name)
        if approved_old is None:
            if any(obj["id"] != own_id for obj in matches):
                raise V2Conflict("unapproved existing Drive target")
            plan[name] = None
            continue
        if not isinstance(approved_old, dict) or set(approved_old) != {
            "file_id", "version", "sha256", "backup_name",
        }:
            raise V2Conflict("incomplete owner old-revision grant")
        old_id = approved_old["file_id"]
        old_version = approved_old["version"]
        old_sha = approved_old["sha256"]
        backup_name = approved_old["backup_name"]
        suffix = PurePosixPath(name).suffix
        stem = name[:-len(suffix)] if suffix else name
        required_backup = stem + "(舊)" + suffix
        dated_backup = bool(re.fullmatch(
            re.escape(stem) + r"\(舊-[0-9]{8}T[0-9]{6}Z\)" + re.escape(suffix),
            backup_name)) if isinstance(backup_name, str) else False
        if (dated_backup and not gateway.named(parent, required_backup)):
            raise V2Conflict("timestamped backup requires an existing (舊) collision")
        if (not all(isinstance(x, str) and x for x in
                    (old_id, old_version, old_sha, backup_name))
                or len(old_sha) != 64
                or any(c not in "0123456789abcdef" for c in old_sha)
                or (backup_name != required_backup and not dated_backup)
                or "/" in backup_name
                or backup_name in backups or old_id in ids or old_id == own_id):
            raise V2Conflict("unsafe or duplicated owner backup selection")
        ids.add(old_id)
        backups.add(backup_name)
        if any(obj["id"] not in {old_id, own_id} for obj in matches):
            raise V2Conflict("unapproved concurrent Drive target")
        old = gateway.get(old_id)
        if (not old or old.get("trashed") or old.get("parents") != [parent]
                or old.get("name") not in {name, backup_name}):
            raise V2Conflict("old Drive revision identity or location changed")
        if old["name"] == name and str(old.get("version")) != old_version:
            raise V2Conflict("old Drive revision version changed")
        if _sha256(gateway.readback(old_id)) != old_sha:
            raise V2Conflict("old Drive revision bytes changed")
        if any(obj["id"] != old_id for obj in gateway.named(parent, backup_name)):
            raise V2Conflict("pre-existing old-backup name collision")
        if not any(obj["id"] == old_id for obj in matches) and old["name"] != backup_name:
            raise V2Conflict("old Drive revision no longer named as attested")
        plan[name] = {"file_id": old_id, "sha256": old_sha,
                      "version": old_version, "backup_name": backup_name}
    return plan


def verify_and_backup_old(*, gateway: Any, parent: str, name: str,
                          selected: dict | None, check_source) -> dict | None:
    """Resumable rename: readback original bytes before & after the rename."""
    if selected is None:
        return None
    old_id = selected["file_id"]
    remote = gateway.get(old_id)
    if (not remote or remote.get("parents") != [parent] or remote.get("trashed")
            or remote.get("name") not in {name, selected["backup_name"]}
            or _sha256(gateway.readback(old_id)) != selected["sha256"]):
        raise V2Conflict("old Drive backup attestation no longer valid")
    if remote["name"] == name:
        if str(remote.get("version")) != selected["version"]:
            raise V2Conflict("old Drive revision version changed after preflight")
        if gateway.named(parent, selected["backup_name"]):
            raise V2Conflict("old Drive backup name collision")
        check_source()
        gateway.promote(old_id, selected["backup_name"])
    remote = gateway.get(old_id)
    if (not remote or remote.get("name") != selected["backup_name"]
            or remote.get("parents") != [parent] or remote.get("trashed")
            or _sha256(gateway.readback(old_id)) != selected["sha256"]):
        raise V2Conflict("old Drive backup readback did not verify")
    return {"original_name": name, "backup_name": selected["backup_name"],
            "file_id": old_id, "sha256": selected["sha256"]}
