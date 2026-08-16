"""Normalize actual correction routing evidence for manifests and UI/audit consumers."""
import json
from pathlib import Path
from typing import Any
GEMINI="gemini-3.7-flash"

def summarize_routing(job_dir: Path) -> dict[str, Any]:
    path=job_dir/"correction-routing.json"
    try: payload=json.loads(path.read_text())
    except (FileNotFoundError,OSError,json.JSONDecodeError):
        return {"correction_policy":"GEMINI_FIRST","correction_initial_provider":GEMINI,"correction_route":GEMINI,"correction_models_used":[GEMINI],"correction_segment_counts":{},"correction_routing_manifest":None}
    initial=str(payload.get("initial_provider") or GEMINI); route=[initial]
    switches=payload.get("provider_switches",[])
    if isinstance(switches,list):
        for switch in switches:
            if isinstance(switch,dict):
                target=str(switch.get("to") or "").strip()
                if target and target!=route[-1]: route.append(target)
    counts=payload.get("segment_counts",{}); counts=counts if isinstance(counts,dict) else {}
    return {"correction_policy":str(payload.get("requested_policy") or "GEMINI_FIRST"),"correction_initial_provider":initial,"correction_route":" -> ".join(route),"correction_models_used":route,"correction_segment_counts":counts,"correction_routing_manifest":"correction-routing.json"}
