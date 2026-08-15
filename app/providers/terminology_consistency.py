"""Deterministic provider-independent terminology consistency audit; report-only."""
import json
from pathlib import Path
from typing import Any

def _load(path,default):
    try:return json.loads(path.read_text())
    except (FileNotFoundError,OSError,json.JSONDecodeError):return default

def build_report(job_dir:Path)->dict[str,Any]:
    corrected=_load(job_dir/"subtitles-corrected.json",{}); glossary=_load(job_dir/"glossary"/"global-terms.json",{})
    segments=corrected.get("segments",[]) if isinstance(corrected,dict) else []; terms=glossary.get("terms",[]) if isinstance(glossary,dict) else []
    issues=[]
    for term in terms if isinstance(terms,list) else []:
        if not isinstance(term,dict):continue
        canonical=str(term.get("canonical") or "").strip()
        if not canonical:continue
        forms=[canonical]
        for raw in term.get("variants",[]):
            value=str(raw).strip()
            if value and value not in forms:forms.append(value)
        observed={}
        for form in forms:
            ids=[str(seg.get("segment_id") or "") for seg in segments if isinstance(seg,dict) and form in str(seg.get("corrected_text") or seg.get("text") or "")]
            if ids:observed[form]=ids
        if len(observed)>1:
            ids=[]
            for values in observed.values():
                for sid in values:
                    if sid and sid not in ids:ids.append(sid)
            issues.append({"canonical":canonical,"variants":list(observed),"occurrences":{k:len(v) for k,v in observed.items()},"segment_ids":ids,"confidence":str(term.get("confidence") or "low"),"suggested_action":"review"})
    return {"schema_version":1,"mode":"report_only","provider_independent":True,"timestamps_modified":False,"segments_modified":False,"issue_count":len(issues),"issues":issues}

def run_terminology_consistency(job_dir:Path)->dict[str,Any]:
    report=build_report(job_dir); path=job_dir/"terminology-consistency.json"; tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n"); tmp.replace(path); return report
