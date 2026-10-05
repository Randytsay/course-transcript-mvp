from __future__ import annotations
import argparse,json,re,sqlite3
from datetime import UTC,datetime
from pathlib import Path

def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def write(path,payload):
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(path)

def text_of(job_dir):
    p=job_dir/"subtitles-cleaned.srt"
    if not p.is_file():p=job_dir/"subtitles.srt"
    if not p.is_file():return ""
    rows=[]
    for raw in p.read_text(encoding="utf-8",errors="ignore").splitlines():
        s=raw.strip()
        if not s or s.isdigit() or "-->" in s:continue
        rows.append(s)
    return "\n".join(rows)

def core(name):
    s=re.sub(r"[™®]","",str(name or "")).strip()
    return s.split(" - ",1)[0].strip()

def tokens(name):
    out=[]
    for t in re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}",core(name)):
        if t.lower() not in {"the","and","with"} and t not in out:out.append(t)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--batch-id",required=True)
    ap.add_argument("--database",default="/app/data/course-transcript.db");ap.add_argument("--data-dir",default="/app/data")
    a=ap.parse_args();data=Path(a.data_dir)
    db=sqlite3.connect(a.database);db.row_factory=sqlite3.Row
    rows=db.execute("select id,source_name from jobs where batch_id=? order by queue_position",(a.batch_id,)).fetchall()
    summary={"PASS":0,"BLOCKED":0}
    for row in rows:
        d=data/"jobs"/row["id"];snap=load(d/"market-america-terminology.json",{});txt=text_of(d)
        products=snap.get("products") if isinstance(snap,dict) else []
        products=products if isinstance(products,list) else []
        literal=[];brand=[]
        for p in products:
            if not isinstance(p,dict):continue
            name=str(p.get("name") or "").strip();c=core(name)
            if c and c in txt:literal.append({"sku":p.get("sku"),"canonical_name":name,"matched":c})
            hit=[t for t in tokens(name) if t in txt]
            if hit:brand.append({"sku":p.get("sku"),"canonical_name":name,"matched_tokens":hit})
        snapshot_status=str(snap.get("status") or "").lower()
        ok=snapshot_status in {"ready","not_applicable"} and bool(txt)
        payload={"schema_version":1,"generated_at":datetime.now(UTC).isoformat(),"content_mode":"market_america_training","source_name":row["source_name"],"status":"PASS" if ok else "BLOCKED","blocking_issue_count":0 if ok else 1,"provider_independent":True,"segments_modified":False,"timestamps_modified":False,"shopclaw_snapshot_status":snap.get("status"),"shopclaw_product_count":len(products),"literal_canonical_hits":literal,"stable_brand_token_hits":brand,"profile_scope":{"applied":["market_america_training","general_chinese"],"excluded":["dacheng_buddhist","buddhist_golden_rules"]},"policy":"ShopClaw identity is spelling evidence only; no fuzzy replacement or claim expansion."}
        write(d/"ma-terminology-gate.json",payload);summary[payload["status"]]+=1
    print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__":main()
