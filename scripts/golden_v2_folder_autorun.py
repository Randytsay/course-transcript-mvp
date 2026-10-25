"""Scoped $6-per-explicit-folder quote+approve entry, using the private Owner CLI.

Every run requires a newly provided explicit Google Drive folder link.
It does not reuse old paid grants or create a background watcher.
This script only queues legally approved V2 pipeline work. Final QA and
Drive publishing require separate evidence and owner-signed source grants.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.golden_v2_folder_budget import (
    MAX_FOLDER_CENTS, validate_folder_decision, save_immutable_budget_decision, _encode,
)

# The trusted private wrapper lives in the existing WebCodex project,
# outside the Git release tree. Run from that project working directory;
# never copy the private config into this checkout.
ENTRY = Path.cwd() / "golden-v2-chat" / "entry.py"


def _run_owner(action: str, *args: str, body: dict | None = None) -> dict:
    import stat
    try:
        info=ENTRY.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                or stat.S_IMODE(info.st_mode)&0o077):
            raise RuntimeError("protected Owner CLI permissions invalid")
    except FileNotFoundError as exc:
        raise RuntimeError("trusted private Owner CLI unavailable") from exc
    argv=[sys.executable,str(ENTRY),action,*args]
    if body is not None:
        argv.append("--request-stdin")
    run=subprocess.run(argv,input=_encode(body) if body is not None else None,
                       capture_output=True,timeout=200)
    if run.returncode != 0:
        # Never echo auth client stdout/stderr, private env, or arbitrary Drive metadata.
        raise RuntimeError("protected owner CLI rejected " + action
                           + "; recheck state before retry")
    try:
        reply=json.loads(run.stdout)
        if not isinstance(reply,dict):raise ValueError
        return reply
    except (ValueError,UnicodeError) as exc:
        raise RuntimeError("owner CLI returned invalid JSON") from exc


def _await_quote(quote_id: str, *, deadline_seconds: int) -> dict:
    until=time.monotonic()+deadline_seconds
    while time.monotonic()<until:
        result=_run_owner("quote-status",quote_id)
        if result.get("state") in {"COMPLETE","OVER_CAP","FAILED"}:
            return result
        if result.get("state") not in {"PENDING","PROCESSING"}:
            raise ValueError("invalid quote state")
        time.sleep(5)
    raise RuntimeError("duration quotation timed out; no paid work started")


def _source_key(preview: dict) -> list:
    return sorted((x["file_id"],str(x["version"]),x["md5_checksum"])
                  for x in preview["files"])


def execute_explicit_folder(
    link: str, *, recursive: bool = False, quote_deadline_seconds: int = 600,
    dry_run: bool = False, owner=_run_owner, await_quote=_await_quote,
    audit_dir: Path | None = None,
) -> dict:
    """No paid request until every source is quoted and the WHOLE folder <=$6.

    The user's act of supplying *this link* is the request scope. The file
    lock on the local decision prevents a replay from silently regranting.
    """
    service=owner("service-status")
    health=owner("drive-health")
    if health.get("status")!="ok":
        raise ValueError("Drive credential/connection not ready")
    if service.get("intake_worker",{}).get("state")!="running":
        raise RuntimeError("intake worker not healthy; stop before new paid work")
    preview=owner("preview",link,*(['--recursive'] if recursive else []))
    if not preview.get("files"):
        raise ValueError("no new audio files found")
    quotes=[]
    for file in preview["files"]:
        request={"link":link,"recursive":recursive,
                 "preview_sha256":preview["preview_sha256"],
                 "file_id":file["file_id"],"confirm":True}
        submitted=owner("quote",body=request)
        if submitted.get("state")=="COMPLETE":
            result=submitted
        else:
            result=await_quote(submitted["quote_id"],deadline_seconds=quote_deadline_seconds)
        quotes.append(result)
    # The Drive folder is observed AGAIN after provider-free intake/quote.
    updated=owner("preview",link,*(['--recursive'] if recursive else []))
    if updated.get("preview_sha256")!=preview["preview_sha256"] or _source_key(updated)!=_source_key(preview):
        raise ValueError("source folder changed during preflight; no paid work started")
    decision=validate_folder_decision(
        preview=preview,quote_responses=quotes,requested_link=link)
    result={"policy_id":decision["policy_id"],"decision_sha256":decision["decision_sha256"],
            "quoted_total_cents":decision["quoted_total_cents"],
            "source_count":decision["source_count"],
            "auto_authorized_count":0,
            "status":"PREPARED_NO_PAID_WORK" if dry_run else "READY_TO_AUTHORIZE"}
    if dry_run:
        return result
    audit_dir = audit_dir or (Path.home()/".local"/"share"/"golden-v2"/"folder-budget-audit")
    audit_path = save_immutable_budget_decision(decision, audit_dir)
    result["audit_path"] = str(audit_path)
    capability=owner("service-status")
    if not capability.get("capabilities",{}).get("paid_execution"):
        raise RuntimeError("PAID_API_DISABLED or paid worker unhealthy; quote done, "
                           "but NO paid authorization executed")
    for item in decision["items"]:
        job=owner("job-status",item["job_id"])
        state=job.get("job",job)
        if state.get("status") in {"QUEUED","PROCESSING","NEEDS_REVIEW","DELIVERED"}:
            # Reused source is never re-submitted; still part of batch quote.
            continue
        if (state.get("status")!="AWAITING_APPROVAL"
                or state.get("source_sha")!=item["source_sha256"]
                or type(state.get("revision")) is not int):
            raise RuntimeError("job state/source changed; no further grant allowed")
        grant={"revision":state["revision"],
               "source_sha256":item["source_sha256"],
               "quote_sha256":item["quote_sha256"],
               "approved_cents":item["quoted_cents"],
               "reference":decision["policy_id"]+":"+decision["decision_sha256"][:24],
               "confirm":True}
        # Protected Owner CLI signs one exact source with independently
        # versioned quote; provider worker never receives raw user prompt.
        reply=owner("authorize-paid",item["job_id"],body=grant)
        if reply.get("status")!="QUEUED":
            raise RuntimeError("unknown paid grant outcome; query ledger before retry")
        result["auto_authorized_count"]+=1
    result["status"]="AUTHORIZED_WITHIN_EXPLICIT_FOLDER_CAP"
    return result


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("link",help="Explicit fresh Google Drive folder URL")
    parser.add_argument("--recursive",action="store_true")
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--quote-deadline-seconds",type=int,default=600)
    opts=parser.parse_args(argv)
    if not 30<=opts.quote_deadline_seconds<=3600:
        parser.error("bounded quote deadline required")
    try:
        outcome=execute_explicit_folder(
            opts.link,recursive=opts.recursive,
            quote_deadline_seconds=opts.quote_deadline_seconds,
            dry_run=opts.dry_run)
    except (ValueError,RuntimeError,KeyError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status":"BLOCKED","reason":str(exc),
                          "max_new_quoted_cost_cents":MAX_FOLDER_CENTS,
                          "provider_outcome_may_require_reconciliation":True},
                         ensure_ascii=False))
        return 2
    print(json.dumps(outcome,ensure_ascii=False,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
