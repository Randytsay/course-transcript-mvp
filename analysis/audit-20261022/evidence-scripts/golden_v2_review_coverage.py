"""Per-job audio coverage: what fraction of audio was actually reviewable?

Three questions per job:
 1. What audio did the Gemini reference text cover?
 2. What audio did the comparison actually place candidates in?
 3. What audio was therefore NEVER subject to any review?
"""
import json,hashlib,sqlite3
from pathlib import Path
JOBS={'251025':('v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be',8883.67),
      '251018':('v2p_560465ab6db05e3da593837f4d76c630',13254.075),
      '261017':('v2p_62a124bdd4595aca91c53085c3732ca7',10781.82)}
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
c=sqlite3.connect('file:/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
out={}
for label,(jid,duration) in JOBS.items():
    j=ROOT/jid
    prim_sha=c.execute("select evidence_sha from v2_stages where job_id=? and name='gemini_primary'",(jid,)).fetchone()['evidence_sha']
    bound=None
    for p in sorted((j/'golden-v2').glob('primary-stage*.json')):
        if hashlib.sha256(p.read_bytes()).hexdigest()==prim_sha: bound=p
    d=json.loads(bound.read_text())
    ev=d.get('window_evidence') or []
    # 1. audio covered by reference text (a window with text contributes its span)
    ref_spans=[]
    for item in ev:
        w=item['identity']['window']
        ident=item['identity']
        slot=hashlib.sha256(json.dumps({k:ident[k] for k in ('model','source_sha256','window')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        rp=j/'gemini-primary'/f'{slot}.json'
        raw=json.loads(rp.read_text()) if rp.exists() else {}
        if (raw.get('text') or '').strip():
            ref_spans.append((w['start_seconds'],w['end_seconds'],len(raw['text']),raw.get('finish_reason')))
    ref_audio=sum(min(e,duration)-s for s,e,_,_ in ref_spans)
    # 2. comparison coverage
    comp=json.loads((j/'golden-v2/engine-comparison.json').read_text())
    cands=comp.get('candidates',[])
    timed=[x for x in cands if x.get('source_time_start') is not None]
    cand_audio=0.0
    if timed:
        spans=sorted((x['source_time_start']/1000,x['source_time_end']/1000) for x in timed)
        cur_s,cur_e=spans[0]
        for s,e in spans[1:]:
            if s<=cur_e: cur_e=max(cur_e,e)
            else: cand_audio+=cur_e-cur_s; cur_s,cur_e=s,e
        cand_audio+=cur_e-cur_s
    last=max((x['source_time_end'] for x in timed),default=0)/1000
    # Honest blind-spot metric: audio that has NO Gemini reference text at all.
    # Alignment can only detect a difference where a reference exists.
    unreferenced_s=sum(max(0.0,min(e,duration)-s) for s,e,_,_ in
                       [(w['start_seconds'],w['end_seconds'],0,0) for w in (d.get('windows') or [])]
                       if not any(rs<=w['start_seconds'] and w['end_seconds']<=re for rs,re,_,_ in ref_spans))
    out[label]={'job_id':jid,'total_audio_s':duration,
      'gemini_reference_windows':[{'span':[s,e],'chars':ch,'finish':f} for s,e,ch,f in ref_spans],
      'reference_audio_s':round(ref_audio,1),'reference_coverage_pct':round(100*ref_audio/duration,1),
      'audio_without_any_reference_s':round(unreferenced_s,1),
      'audio_without_any_reference_pct':round(100*unreferenced_s/duration,1),
      'comparison_candidates':len(cands),'timed_candidates':len(timed),'untimed_candidates':len(cands)-len(timed),
      'comparison_reach_s':last,'comparison_reach_pct':round(100*last/duration,1)}
tot=sum(out[k]['comparison_candidates'] for k in out)
print(json.dumps({'per_job':out,'total_candidates_across_jobs':tot,
 'matches_4091_rubber_stamp_total':tot==4091},ensure_ascii=False,indent=2))
