from __future__ import annotations
import argparse, hashlib, json, sqlite3
from pathlib import Path
from app.jobs.drive_publish import _artifact_for, publish_outputs, source_parent_destination

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()

def write(path,payload):
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(path)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--batch-id",required=True)
    ap.add_argument("--database",default="/app/data/course-transcript.db");ap.add_argument("--data-dir",default="/app/data")
    a=ap.parse_args();data=Path(a.data_dir)
    db=sqlite3.connect(a.database);db.row_factory=sqlite3.Row
    rows=db.execute("select id,source_name,source_path from jobs where batch_id=? order by queue_position",(a.batch_id,)).fetchall()
    changed=[];skipped=[];failed=[]
    for row in rows:
        d=data/"jobs"/row["id"];state_path=d/"drive-publish-state.json"
        if not state_path.is_file():
            failed.append({"source":row["source_name"],"error":"missing_state"});continue
        state=json.loads(state_path.read_text(encoding="utf-8"))
        art=_artifact_for(d,"srt");local=d/art.local_name;current_sha=sha(local)
        old=((state.get("files") or {}).get("srt") or {}).get("sha256")
        if old==current_sha:
            skipped.append(row["source_name"]);continue
        rec=((state.setdefault("files",{})).setdefault("srt",{}))
        rec.update({"status":"pending","phase":"checking_final","backup_remote_path":None,"backup_bytes":None,"error":None})
        state["status"]="in_progress";write(state_path,state)
        try:
            result=publish_outputs(d,source_name=row["source_name"],destination=state.get("destination") or source_parent_destination(row["source_path"]),output_formats=["srt"],authorized=True)
            changed.append({"source":row["source_name"],"local_name":art.local_name,"sha256":current_sha,"backup":((result.get("files") or {}).get("srt") or {}).get("backup_remote_path")})
        except Exception as exc:
            failed.append({"source":row["source_name"],"error":type(exc).__name__+":"+str(exc)})
    print(json.dumps({"republished":len(changed),"unchanged":len(skipped),"failed":len(failed),"changed":changed,"failures":failed},ensure_ascii=False))
if __name__=="__main__":main()
