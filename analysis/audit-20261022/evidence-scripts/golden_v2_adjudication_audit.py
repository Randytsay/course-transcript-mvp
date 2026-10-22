"""Audit the 4,091 KEEP_CHIRP adjudications for real, traceable acoustic evidence."""
import json,collections,hashlib
from pathlib import Path
JOBS={'251025':'v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be',
      '251018':'v2p_560465ab6db05e3da593837f4d76c630',
      '261017':'v2p_62a124bdd4595aca91c53085c3732ca7'}
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
out={}
grand=collections.Counter()
for label,jid in JOBS.items():
    j=ROOT/jid
    p=j/'golden-v2/adjudications.json'
    if not p.exists():
        out[label]={'adjudications_file':'MISSING'};continue
    d=json.loads(p.read_text())
    adjs=d.get('adjudications',[])
    reasons=collections.Counter(a.get('decision_reason','') for a in adjs)
    decisions=collections.Counter(a.get('decision','') for a in adjs)
    statuses=collections.Counter(a.get('status','') for a in adjs)
    reviewers=collections.Counter(a.get('reviewer','') for a in adjs)
    refs=collections.Counter(a.get('review_reference','') for a in adjs)
    # window formula check: is reviewed_audio_window_ms a mechanical +/-1s of the candidate?
    comp=json.loads((j/'golden-v2/engine-comparison.json').read_text())
    cands=comp.get('candidates',[])
    mech=0; checked=0; covering=0; timed=0
    for a in adjs:
        i=a.get('candidate_index')
        if not isinstance(i,int) or i>=len(cands): continue
        c=cands[i]; b=a.get('reviewed_audio_window_ms')
        if not (isinstance(b,list) and len(b)==2): continue
        checked+=1
        s=c.get('source_time_start');e=c.get('source_time_end')
        if s is not None and e is not None:
            timed+=1
            if b[0]==max(0,s-1000) and b[1]==e+1000: mech+=1
            if b[0]<=s and e<=b[1]: covering+=1
    out[label]={'file_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
      'status':d.get('status'),'reviewer':d.get('reviewer'),'review_reference':d.get('review_reference'),
      'revision':d.get('revision'),'comparison_sha256':d.get('comparison_sha256'),
      'adjudication_count':len(adjs),
      'decisions':dict(decisions),'statuses':dict(statuses),
      'distinct_reasons':len(reasons),'top_reason':reasons.most_common(1),
      'distinct_reviewers':dict(reviewers),'distinct_review_references':dict(refs),
      'audio_evidence_checked_true':sum(1 for a in adjs if a.get('audio_evidence_checked') is True),
      'windows_checked':checked,'windows_mechanical_plus_minus_1s':mech,
      'windows_actually_covering_candidate':covering,'timed_candidates_in_window_set':timed,
      'replacements':len(d.get('replacements',[]))}
    grand['adjudications']+=len(adjs);grand['reasons']+=len(reasons)
print(json.dumps({'per_job':out,'grand_total_adjudications':grand['adjudications']},ensure_ascii=False,indent=2))
