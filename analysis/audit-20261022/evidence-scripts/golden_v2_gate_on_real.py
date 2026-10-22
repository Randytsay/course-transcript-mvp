"""Prove the new gates BLOCK the three real audited production artifacts,
and that a healthy comparison still passes. Run against the live job dirs.
"""
import json,sys
from pathlib import Path
sys.path.insert(0,'/srv/ai-workspace/projects/golden-v2-fix-20261021')
from app.pipeline.golden_v2_alignment_health import assess_alignment_health
from app.pipeline.golden_v2_review_evidence import assess_review_evidence, ReviewEvidenceError
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
for lbl,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    G=V2/jid/'golden-v2'
    c=json.loads((G/'engine-comparison.json').read_text())
    a=json.loads((G/'adjudications.json').read_text())
    p=json.loads((G/'primary-stage.json').read_text())
    # coverage from real window evidence
    ev=p.get('window_evidence') or []
    cov=0.0; tot=0.0
    for e in ev:
        w=(e.get('identity') or {}).get('window') or {}
        s,en=w.get('start_seconds'),w.get('end_seconds')
        if not isinstance(s,(int,float)) or not isinstance(en,(int,float)): continue
        tot=max(tot,en*1000.0)
        if e.get('finish_reason')=='STOP': cov+=(en-s)*1000.0
    ratio=(cov/tot) if tot else None
    al=assess_alignment_health(candidates=c.get('candidates') or [],
        comparison=c, primary_coverage_ratio=ratio,
        duration_ms=json.loads((G/'chirp-merged.json').read_text()).get('duration_ms'))
    print(f'{lbl}: coverage={ratio}  ALIGNMENT_GATE={al["status"]} reasons={al["reasons"]} '
          f'large_offset={al["large_offset_candidate_count"]}/{al["candidate_count"]} '
          f'median_offset={al["median_char_offset"]}')
    try:
        assess_review_evidence(candidates=c.get('candidates') or [],
                               decisions=a.get('adjudications') or [])
        print(f'   REVIEW_EVIDENCE_GATE=PASS  <-- BAD, should have blocked')
    except ReviewEvidenceError as exc:
        print(f'   REVIEW_EVIDENCE_GATE=BLOCKED ({exc})')
    print()
