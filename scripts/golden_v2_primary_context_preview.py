"""Read-only Gemini 3.8 full-context diagnostic. No paid APIs or transcript text."""
import argparse
import json
import sqlite3
from pathlib import Path
from app.providers.golden_v2_audio_staging import file_sha256
from app.pipeline.golden_v2_primary_stage import _primary_audit_path

def inspect_primary_context(*,database,job_dir,job_id):
    db=Path(database)
    if not db.is_absolute() or not db.is_file() or db.is_symlink():
        raise ValueError("existing absolute non-symlink V2 ledger required")
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro",uri=True) as c:
        c.row_factory=sqlite3.Row
        j=c.execute("SELECT source_sha,status FROM v2_jobs WHERE id=?",(job_id,)).fetchone()
        stage=c.execute("SELECT state,evidence_sha,actual_cents FROM v2_stages "
                        "WHERE job_id=? AND name='gemini_primary'",(job_id,)).fetchone()
        if j is None or stage is None:raise ValueError("unknown Gemini V2 job")
        source_sha=j["source_sha"]
        stage=dict(stage)
    folder=Path(job_dir)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("unsafe job folder")
    path=folder/"golden-v2"/"primary-stage.json"
    if not path.is_file() or path.is_symlink():
        return {"job_id":job_id,"gemini_stage":stage["state"],"report":"MISSING",
                "planned_windows":0,"saved_response_windows":0,
                "provider_calls_started":False,"ledger_modified":False}
    report=json.loads(path.read_bytes())
    if (report.get("schema")!="golden-v2-primary-stage-v1"
            or report.get("job_id")!=job_id or report.get("source_sha256")!=source_sha
            or report.get("mode")!="gemini_3_8_audio_first"):
        raise ValueError("source-bound Gemini primary report invalid")
    windows, receipts=report.get("windows"),report.get("window_evidence")
    if not isinstance(windows,list) or not isinstance(receipts,list) or len(receipts)>len(windows):
        raise ValueError("Gemini report window evidence corrupt")
    results=[]
    for i,window in enumerate(windows):
        if i>=len(receipts):
            results.append({"index":i,"status":"NO_PROVIDER_RESPONSE_SAVED"})
            continue
        e=receipts[i]
        try:
            identity=e["identity"]
            audit=_primary_audit_path(folder,identity)
            if (audit.is_symlink() or not audit.is_file()
                    or file_sha256(audit)!=e["raw_audit_sha256"]
                    or identity["source_sha256"]!=source_sha
                    or identity["window"]!={k:window[k] for k in
                        ("index","start_seconds","end_seconds","gcs_uri","mime_type")}):
                raise ValueError("receipt mismatch")
            record=json.loads(audit.read_bytes())
            if record["identity"]!=identity:raise ValueError("audit identity changed")
            results.append({"index":i,"status":record.get("status","UNKNOWN"),
                            "finish_reason":record.get("finish_reason",""),
                            "text_chars":len(record.get("text","")),
                            "raw_sha_verified":True})
        except (KeyError,TypeError,OSError,ValueError):
            results.append({"index":i,"status":"EVIDENCE_UNVERIFIED",
                            "raw_sha_verified":False})
    assembled=report.get("assembled",{})
    return {"job_id":job_id,"gemini_stage":stage["state"],
            "stage_report_sha_verified":file_sha256(path)==stage["evidence_sha"],
            "planned_windows":len(windows),"saved_response_windows":len(receipts),
            "structural_coverage":assembled.get("structural_coverage","UNVERIFIED"),
            "audio_coverage":assembled.get("acoustic_coverage","UNVERIFIED"),
            "ready_for_chirp":report.get("ready_for_chirp") is True,
            "windows":results,"provider_calls_started":False,"ledger_modified":False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database",required=True,type=Path)
    p.add_argument("--job-dir",required=True,type=Path)
    p.add_argument("--job-id",required=True)
    a=p.parse_args()
    print(json.dumps(inspect_primary_context(database=a.database,
          job_dir=a.job_dir,job_id=a.job_id),ensure_ascii=False,sort_keys=True))

if __name__=="__main__":
    main()
