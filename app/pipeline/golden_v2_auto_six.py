"""Owner-persisted US$6 ceiling per explicitly submitted Drive folder.

Only the existing authenticated owner client may trigger new file approvals.
No background discovery, automatic uploads or provider POSTs live here.
No chat memory may substitute for the server HMAC+SQLite policy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

from app.jobs.golden_v2_ledger import GoldenV2Ledger, V2Conflict, approved_trial_exposure
from app.pipeline.golden_v2_pricing import assert_current, PRICE_VERSION
from app.pipeline.golden_v2_quote_intake import wire
from app.pipeline.golden_v2_runtime_services import signed_grant
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_immutable import store_immutable_bytes

POLICY_ID = "explicit-folder-600c-20261025"
CAP_CENTS = 600
MODE = "FLASH_CHIRP"
ALLOWED_MODELS = ["gemini-3.8-flash", "chirp_3"]
SCOPE = "OWNER_SUBMITTED_FOLDER_ONLY"


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS v2_auto_folder_policy(
        policy_id TEXT PRIMARY KEY, actor TEXT NOT NULL, grant_json TEXT NOT NULL,
        signature TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS v2_auto_folder_reservations(
        job_id TEXT PRIMARY KEY REFERENCES v2_jobs(id), folder_key TEXT NOT NULL,
        policy_id TEXT NOT NULL, source_sha TEXT NOT NULL, quote_sha TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents BETWEEN 1 AND 600),
        approved_at TEXT NOT NULL
    )""")


def policy_status(root: Path) -> dict:
    ledger = GoldenV2Ledger.open_existing(Path(root)/"ledger.sqlite")
    with ledger._tx() as db:
        _schema(db)
        row=db.execute("SELECT * FROM v2_auto_folder_policy WHERE policy_id=?",(POLICY_ID,)).fetchone()
        if row is None:
            return {"configured": False, "enabled": False, "cap_per_submitted_folder_cents":CAP_CENTS}
        payload=json.loads(row["grant_json"])
        valid=hmac.compare_digest(signed_grant(payload)["signature"],row["signature"])
        return {"configured": valid, "enabled": bool(row["enabled"]) and valid,
                "policy_id": POLICY_ID, "cap_per_submitted_folder_cents":CAP_CENTS,
                "scope":SCOPE,"actor":row["actor"],"valid_through":payload["valid_through"]}


def configure_policy(*, root: Path, actor: str, enabled: bool, valid_through: str) -> dict:
    """Explicit owner-authenticated one-time setup; no silent cap escalation."""
    expiry=date.fromisoformat(valid_through)
    now=datetime.now(timezone.utc).date()
    if (not actor.strip() or type(enabled) is not bool or expiry<now or
            expiry>date(2027,12,31)):
        raise V2Conflict("invalid bounded owner policy expiry/actor")
    payload={"policy_id":POLICY_ID,"actor":actor,"scope":SCOPE,
             "per_folder_limit_cents":CAP_CENTS, "per_job_limit_cents":CAP_CENTS,
             "models":ALLOWED_MODELS, "mode":MODE,
             "valid_through":valid_through,
             "unrequested_folders_disallowed":True,
             "provider_extras_not_included":True}
    envelope=signed_grant(payload)
    ledger=GoldenV2Ledger.open_existing(Path(root)/"ledger.sqlite")
    with ledger._tx() as db:
        _schema(db)
        prior=db.execute("SELECT * FROM v2_auto_folder_policy WHERE policy_id=?",(POLICY_ID,)).fetchone()
        if prior and (prior["grant_json"]!=wire(payload) or
                      not hmac.compare_digest(prior["signature"],envelope["signature"])):
            raise V2Conflict("existing auto policy conflicts; disable, review and migrate explicitly")
        db.execute("""INSERT INTO v2_auto_folder_policy
                      (policy_id,actor,grant_json,signature,enabled) VALUES(?,?,?,?,?)
                      ON CONFLICT(policy_id) DO UPDATE SET enabled=excluded.enabled""",
                   (POLICY_ID,actor,wire(payload),envelope["signature"],int(enabled)))
    return {"configured":True,"enabled":enabled,"policy_id":POLICY_ID,
            "cap_per_submitted_folder_cents":CAP_CENTS,
            "scope":SCOPE,"valid_through":valid_through}


def auto_authorize_quote(*, root: Path, actor: str, job_id: str,
                         submitted_folder_path: str, aggregate_cap_cents: int = 1000) -> dict:
    """Authorize only a completed bound quote in the owner-submitted folder.

    The new per-folder reservation and Golden job approval commit atomically.
    Never calls a provider; the separately authenticated paid worker verifies
    the persisted signed owner-paid-grant before attempting an operation.
    """
    if (not re.fullmatch(r"v2p_[0-9a-f]{32}", job_id) or not actor or
            not isinstance(submitted_folder_path,str) or
            not submitted_folder_path.startswith("gdrive:") or
            len(submitted_folder_path)>1024 or
            type(aggregate_cap_cents) is not int or aggregate_cap_cents<=0):
        raise V2Conflict("invalid explicit owner-selected folder or job")
    base=PurePosixPath(submitted_folder_path.replace("gdrive:","",1))
    if ".." in base.parts or str(base) in ("","."):
        raise V2Conflict("unsafe folder path")
    root=Path(root)
    folder_key="gdrive:"+str(base).rstrip("/")
    jobdir=root/"jobs"/job_id
    qfile=jobdir/"trusted-quote.json"
    if jobdir.is_symlink() or qfile.is_symlink() or not qfile.is_file():
        raise V2Conflict("verified quote unavailable")
    quote_raw=qfile.read_bytes()
    quote=json.loads(quote_raw)
    assert_current()
    estimate=quote.get("estimated_cents")
    source=quote.get("drive_source") or {}
    if (quote.get("mode")!=MODE or
            type(estimate) is not int or not 0<estimate<=CAP_CENTS or
            not quote.get("source_sha256") or
            source.get("source_path","").rsplit("/",1)[0] != folder_key or
            quote.get("valid_through","") < datetime.now(timezone.utc).date().isoformat() or
            quote.get("provider_calls_started") is not False or
            quote.get("price_version") != PRICE_VERSION or
            quote.get("invoice_verified") is not False or
            quote.get("storage_and_network_included") is not False or
            not isinstance(quote.get("estimated_stage_cents"),dict) or
            sum(quote["estimated_stage_cents"].values())!=estimate or
            quote["estimated_stage_cents"].get("gemini_primary",0)<=0 or
            quote["estimated_stage_cents"].get("chirp_independent",0)<=0 or
            quote["estimated_stage_cents"].get("third_optional",0)!=0):
        raise V2Conflict("source folder / price / USD6 quote not eligible")
    digest=hashlib.sha256(quote_raw).hexdigest()
    ledger=GoldenV2Ledger.open_existing(root/"ledger.sqlite")
    status=ledger.status(job_id)
    if (status["status"]!="AWAITING_APPROVAL" or
            status["source_sha"]!=quote["source_sha256"] or
            int(status["estimated_cents"])!=estimate or
            status["approved_cents"] is not None):
        raise V2Conflict("already approved, paid or stale job; reconcile first")
    with ledger._tx() as db:
        _schema(db)
        pol=db.execute("SELECT * FROM v2_auto_folder_policy WHERE policy_id=?",(POLICY_ID,)).fetchone()
        if pol is None or not pol["enabled"] or pol["actor"]!=actor:
            raise V2Conflict("authenticated owner standing policy disabled")
        payload=json.loads(pol["grant_json"])
        if (not hmac.compare_digest(signed_grant(payload)["signature"],pol["signature"]) or
                payload["scope"]!=SCOPE or payload["mode"]!=MODE or
                payload["models"]!=ALLOWED_MODELS or
                payload["per_folder_limit_cents"]!=CAP_CENTS or
                payload["per_job_limit_cents"]!=CAP_CENTS or
                payload["valid_through"]<datetime.now(timezone.utc).date().isoformat()):
            raise V2Conflict("invalid, expired or changed signed policy")
        live=db.execute("SELECT id,status,source_sha,revision,estimated_cents,approved_cents "
                        "FROM v2_jobs WHERE id=?",(job_id,)).fetchone()
        if (not live or live["revision"]!=status["revision"] or
                live["status"]!="AWAITING_APPROVAL" or
                live["approved_cents"] is not None):
            raise V2Conflict("concurrent approval; abort")
        if db.execute("SELECT 1 FROM v2_jobs WHERE source_sha=? AND id<>? "
                      "AND approved_cents IS NOT NULL",(live["source_sha"],job_id)).fetchone():
            raise V2Conflict("source already funded; no automatic repeat")
        total=db.execute("SELECT coalesce(sum(amount_cents),0) FROM "
                         "v2_auto_folder_reservations WHERE folder_key=?",(folder_key,)).fetchone()[0]
        if total+estimate>CAP_CENTS:
            raise V2Conflict("USD6 folder budget exceeded")
        if approved_trial_exposure(db)+estimate>aggregate_cap_cents:
            raise V2Conflict("global paid exposure cap exceeded")
        grant={"revision":live["revision"],"quote_sha256":digest,
               "source_sha256":live["source_sha"],"approved_cents":estimate,
               "reference":"standing:"+POLICY_ID+":"+hashlib.sha256(folder_key.encode()).hexdigest()[:16],
               "job_id":job_id,"actor":actor,
               "standing_policy_id":POLICY_ID,"folder_key_sha256":hashlib.sha256(folder_key.encode()).hexdigest()}
        # Persist grant first, fail closed if immutability conflicts.
        store_immutable_bytes(jobdir/"owner-paid-grant.json",
                              wire(signed_grant(grant)).encode())
        db.execute("INSERT INTO v2_auto_folder_reservations "
                   "(job_id,folder_key,policy_id,source_sha,quote_sha,amount_cents,approved_at) "
                   "VALUES(?,?,?,?,?,?,?)",
                   (job_id,folder_key,POLICY_ID,live["source_sha"],digest,
                    estimate,datetime.now(timezone.utc).isoformat()))
        db.execute("UPDATE v2_jobs SET status='QUEUED', approval_reference=?, "
                   "approved_cents=?,revision=revision+1 WHERE id=? AND revision=? AND status='AWAITING_APPROVAL'",
                   (grant["reference"],estimate,job_id,live["revision"]))
        if db.execute("SELECT changes()").fetchone()[0]!=1:
            raise V2Conflict("concurrent authorization changed")
        return {"job_id":job_id,"state":"QUEUED","approved_cents":estimate,
                "folder_reserved_cents":total+estimate,"folder_cap_cents":CAP_CENTS,
                "policy_id":POLICY_ID,"source_sha256":live["source_sha"],
                "paid_provider_calls_started":False}
