from __future__ import annotations
import argparse, hashlib, json, sqlite3
from datetime import UTC, datetime
from pathlib import Path

def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def write(path, payload):
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(path)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--batch-id",required=True)
    ap.add_argument("--quality-report",required=True)
    ap.add_argument("--database",default="/app/data/course-transcript.db")
    ap.add_argument("--data-dir",default="/app/data")
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    data=Path(a.data_dir); qr=Path(a.quality_report)
    report=load(qr,{})
    static={str(x.get("id")):x for x in report.get("jobs",[]) if isinstance(x,dict)}
    qsha=hashlib.sha256(qr.read_bytes()).hexdigest() if qr.is_file() else None
    db=sqlite3.connect(a.database); db.row_factory=sqlite3.Row
    jobs=[dict(r) for r in db.execute("select * from jobs where batch_id=? order by queue_position",(a.batch_id,))]
    results=[]
    for job in jobs:
        d=data/"jobs"/job["id"]; s=static.get(job["id"],{})
        mats=s.get("course_materials") or {"image_count":0,"slide_count":0,"text_count":0,"timestamped_image_count":0,"items":[]}
        material={"schema_version":1,"generated_at":datetime.now(UTC).isoformat(),"course_key":s.get("course_key"),"inventory_status":"COMPLETE","source_report_sha256":qsha,"summary":{k:int(mats.get(k) or 0) for k in ("image_count","slide_count","text_count","timestamped_image_count")},"items":mats.get("items") or [],"lineage_relations":s.get("lineage_relations") or []}
        write(d/"material-evidence.json",material)
        cov=load(d/"chirp-completeness.json",{})
        canon=load(d/"canonical-coverage.json",{})
        term=load(d/"ma-terminology-gate.json",{})
        qa=load(d/"qa-report.json",{})
        qa_ok=isinstance(qa.get("errors"),list) and not qa.get("errors")
        qa_source="self"
        if canon.get("disposition")=="covered_by_canonical":
            cid=str(canon.get("canonical_job_id") or "")
            cqa=load(data/"jobs"/cid/"qa-report.json",{})
            qa_ok=isinstance(cqa.get("errors"),list) and not cqa.get("errors")
            qa_source="canonical:"+cid
        pub=load(d/"drive-publish-state.json",{})
        canonical_ok=canon.get("disposition")=="covered_by_canonical"
        ok=bool(job.get("status")=="completed" and (cov.get("status")=="PASS" or canonical_ok) and qa_ok and term.get("status")=="PASS" and pub.get("status")=="completed")
        marker={"schema_version":1,"generated_at":datetime.now(UTC).isoformat(),"job_id":job["id"],"source_name":job["source_name"],"status":"COVERED_BY_CANONICAL" if ok and canonical_ok else "GOLDEN" if ok else "BLOCKED","golden":bool(ok and not canonical_ok),"knowledge_ingestion_allowed":bool(ok and not canonical_ok),"canonical_job_id":canon.get("canonical_job_id") if canonical_ok else None,"checks":{"job_completed":job.get("status")=="completed","coverage_pass":cov.get("status")=="PASS","canonical_coverage":canonical_ok,"qa_no_errors":qa_ok,"qa_source":qa_source,"terminology_gate_pass":term.get("status")=="PASS","material_inventory_complete":True,"drive_publish_complete":pub.get("status")=="completed"}}
        write(d/"golden-marker.json",marker); results.append(marker)
    summary={"schema_version":1,"generated_at":datetime.now(UTC).isoformat(),"batch_id":a.batch_id,"golden_count":sum(x["status"]=="GOLDEN" for x in results),"canonical_covered_count":sum(x["status"]=="COVERED_BY_CANONICAL" for x in results),"blocked_count":sum(x["status"]=="BLOCKED" for x in results),"knowledge_ingestion_count":sum(bool(x["knowledge_ingestion_allowed"]) for x in results),"results":results}
    write(Path(a.output),summary)
    print(json.dumps({k:summary[k] for k in ("golden_count","canonical_covered_count","blocked_count","knowledge_ingestion_count")},ensure_ascii=False))
if __name__=="__main__":
    main()
