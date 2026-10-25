"""Signed-owner-only, exact-source AI-limited Drive delivery via shared V2 lock.

Never calls paid providers, relaxes Golden QA, or overwrites any old output.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path

from app.jobs.drive_lock import drive_publish_lock
from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict
from app.pipeline.golden_v2_ai_limited_closeout import (
    TIER, _sha, _wire, _check_file, finalize_readback, _schema,
)
from app.pipeline.golden_v2_delivery import DriveDeliveryGateway
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_immutable import store_immutable_bytes


def _read_proposal(root: Path, job_id: str, manifest_sha: str) -> dict:
    ledger = GoldenV2Ledger.open_existing(root / "ledger.sqlite")
    with ledger._tx() as db:
        _schema(db)
        row = db.execute(
            "SELECT * FROM v2_ai_limited_deliveries "
            "WHERE job_id=? AND manifest_sha=?", (job_id,manifest_sha)
        ).fetchone()
        if row is None or row["state"] not in {"PREPARED", "DELIVERED"}:
            raise V2Conflict("missing prepared independent delivery")
        manifest = json.loads(row["manifest_json"])
    if (manifest.get("schema") != "golden-v2-limited-ai-delivery-v1"
            or manifest.get("quality_tier") != TIER
            or manifest.get("manifest_sha256") != manifest_sha
            or manifest.get("job_id") != job_id):
        raise V2Conflict("AI limited delivery manifest mismatch")
    return manifest


def publish_signed_ai_limited(
    *, root: Path, job_id: str, manifest_sha: str, lock_root: Path,
    gateway=None,
) -> dict:
    """A trusted owner-authenticated route must call this with an HMAC grant.

    Durable pending IDs prevent retries from generating duplicate remote files;
    no target collision or changed preflight source is auto-overwritten.
    """
    root = Path(root)
    if root.is_symlink() or Path(lock_root).is_symlink():
        raise V2Conflict("unsafe publication root")
    job = root / "jobs" / job_id
    if job.is_symlink() or (job / "golden-v2").is_symlink():
        raise V2Conflict("unsafe job / source artifacts")
    manifest = _read_proposal(root,job_id,manifest_sha)
    path = job / "owner-ai-limited-grant-" / (manifest_sha + ".json")
    if path.is_symlink() or not path.is_file():
        raise V2Conflict("separately authenticated owner grant missing")
    envelope = json.loads(_check_file(path,32768))
    payload = envelope.get("grant")
    if not isinstance(payload,dict):
        raise V2Conflict("invalid limited publication grant")
    expected = signed_grant(payload)["signature"]
    if not isinstance(envelope.get("signature"),str) or not hmac.compare_digest(expected,envelope["signature"]):
        raise V2Conflict("owner signature not verified")
    # The ledger registers exact preflight/SRT/QA bytes; any drift halts.
    ledger = GoldenV2Ledger.open_existing(root/"ledger.sqlite")
    state = ledger.status(job_id)
    if (state["revision"] != manifest["ledger_revision"] or
            state["source_sha"] != manifest["source_sha256"] or
            state["status"] != "NEEDS_REVIEW"):
        raise V2Conflict("stale source or job revision")
    folder = job/"golden-v2"
    selected = []
    for filename, digest in [(manifest["source_srt_filename"],manifest["srt_sha256"]),
                              (manifest["source_txt_filename"],manifest["txt_sha256"])]:
        # Exact file name only, no path traversal or unrecorded source content.
        if Path(filename).name != filename:
            raise V2Conflict("unsafe local asset filename")
        data = _check_file(folder/filename,20_000_000)
        if _sha(data) != digest:
            raise V2Conflict("original AI-delivery candidate has changed")
        selected.append(data)
    triage = _check_file(folder/manifest["source_triage_filename"],10_000_000)
    if _sha(triage)!=manifest["triage_sha256"]:
        raise V2Conflict("original unresolved QA evidence has changed")
    preflight = json.loads(_check_file(job/"source-preflight.json",262144))
    obs=preflight["drive_observation"]
    if (obs["file_id"]!=manifest["source_file_id"]
            or str(obs["version"])!=manifest["source_version"]
            or preflight["source_sha256"]!=manifest["source_sha256"]):
        raise V2Conflict("original Drive source metadata drift")
    gateway = gateway or DriveDeliveryGateway()

    def check_source():
        remote = gateway.get(obs["file_id"])
        if (not remote or remote.get("trashed") or
                remote.get("id")!=obs["file_id"] or
                str(remote.get("version"))!=str(obs["version"]) or
                int(remote.get("size",-1))!=int(obs["size_bytes"]) or
                remote.get("md5Checksum") != obs.get("downloaded_md5",
                    preflight.get("original_md5_checksum")) or
                len(remote.get("parents",[]))!=1):
            raise V2Conflict("original Drive source no longer matches preflight")
        return remote["parents"][0]

    remote_files=[]
    with drive_publish_lock(Path(lock_root),preflight["drive_source_path"]):
        parent=check_source()
        plans=[]
        names=manifest["output_names"]
        intent_dir=folder/"ai-limited-intents"/manifest_sha
        if intent_dir.is_symlink():raise V2Conflict("unsafe publish intent")
        intent_dir.mkdir(parents=True,exist_ok=True)
        # Validate both target names before creating either new remote object.
        for suffix,name,raw in zip(("srt","txt"),names,selected):
            intent=intent_dir/(suffix+".json")
            saved=json.loads(intent.read_text()) if intent.is_file() and not intent.is_symlink() else None
            if intent.is_symlink():raise V2Conflict("symlinked Drive publish intent")
            existing=gateway.named(parent,name)
            if saved:
                if (saved["name"]!=name or saved["parent_id"]!=parent or
                        saved["sha256"]!=_sha(raw)):
                    raise V2Conflict("idempotent Drive intent differs")
                file_id=saved["file_id"]
            else:
                if existing:raise V2Conflict("destination name already exists")
                file_id=gateway.generate_id()
                saved={"file_id":file_id,"parent_id":parent,"name":name,
                       "pending_name":"."+job_id+".ai-limited."+manifest_sha[:12]+"."+suffix,
                       "sha256":_sha(raw)}
                store_immutable_bytes(intent,_wire(saved))
            if any(item["id"]!=file_id for item in existing):
                raise V2Conflict("unrelated or user-edited destination exists")
            plans.append((saved,raw))
        for saved,raw in plans:
            file_id=saved["file_id"];name=saved["name"]
            remote=gateway.get(file_id)
            if remote is None:
                check_source()
                gateway.create_pending(file_id,parent,saved["pending_name"],raw,job_id)
                remote=gateway.get(file_id)
            if (not remote or remote.get("trashed") or
                    remote.get("parents")!=[parent] or
                    (remote.get("appProperties") or {}).get("golden_v2_job")!=job_id or
                    remote.get("name") not in {saved["pending_name"],name}):
                raise V2Conflict("pending remote Drive ID ownership changed")
            observed=gateway.readback(file_id)
            if not isinstance(observed,bytes):
                raise V2Conflict("remote readback unavailable")
            if _sha(observed)!=saved["sha256"]:
                if remote["name"] != saved["pending_name"] or observed:
                    raise V2Conflict("published file was externally modified")
                check_source()
                gateway.write_pending_content(file_id,raw)
                if _sha(gateway.readback(file_id))!=saved["sha256"]:
                    raise V2Conflict("pending upload cannot be verified")
        # Promote only after both pending contents are checked.
        for saved,raw in plans:
            check_source()
            file_id=saved["file_id"];name=saved["name"]
            remote=gateway.get(file_id)
            if remote.get("name")==saved["pending_name"]:
                if gateway.named(parent,name):
                    raise V2Conflict("new colliding target before promote")
                gateway.promote(file_id,name)
            verified=gateway.get(file_id)
            if (not verified or verified.get("name")!=name or
                    verified.get("parents")!=[parent] or
                    any(x["id"]!=file_id for x in gateway.named(parent,name)) or
                    _sha(gateway.readback(file_id))!=saved["sha256"]):
                raise V2Conflict("promoted Drive identity or SHA changed")
            remote_files.append({"name":name,"id":file_id})
        check_source()
        def remote_verify(name,file_id,digest):
            obj=gateway.get(file_id)
            return (obj is not None and not obj.get("trashed") and
                    obj.get("name")==name and obj.get("parents")==[parent] and
                    _sha(gateway.readback(file_id))==digest)
        receipt=finalize_readback(
            root=root,manifest=manifest,remote_files=remote_files,
            owner_grant=payload,signed_grant_verifier=lambda g: (
                g==payload and hmac.compare_digest(signed_grant(g)["signature"],envelope["signature"])
            ),readback_verifier=remote_verify,
        )
        receipt["public_links_created"]=False
        return receipt
