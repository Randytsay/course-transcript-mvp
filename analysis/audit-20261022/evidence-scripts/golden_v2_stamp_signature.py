"""Verify the exact rubber-stamp signature so the gate is grounded in real data:
- is reviewed_audio_window_ms == [start-1000, end+1000]?
- do untimed candidates (source_time_start is None) carry fabricated windows?
"""
import json
from pathlib import Path
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
for label,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                  ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                  ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    G=V2/jid/'golden-v2'
    c=json.loads((G/'engine-comparison.json').read_text())
    a=json.loads((G/'adjudications.json').read_text())
    cands=c['candidates']
    adjs={e['candidate_index']:e for e in a.get('adjudications',[])}
    mech=0; untimed_with_window=0; untimed_total=0; checked=0
    for i,cd in enumerate(cands):
        e=adjs.get(i)
        if not e: continue
        w=e.get('reviewed_audio_window_ms')
        s,t=cd.get('source_time_start'),cd.get('source_time_end')
        if s is None or t is None:
            untimed_total+=1
            if isinstance(w,list) and len(w)==2: untimed_with_window+=1
        else:
            checked+=1
            if isinstance(w,list) and len(w)==2 and w==[s-1000,t+1000]: mech+=1
    print(f'{label}: timed_adjudicated={checked} mechanical_[s-1000,e+1000]={mech} '
          f'({100*mech/max(1,checked):.1f}%) | untimed={untimed_total} '
          f'with_fabricated_window={untimed_with_window} '
          f'({100*untimed_with_window/max(1,untimed_total):.1f}%)')
    # decision_reason uniqueness across candidates
    reasons={}
    for e in adjs.values(): reasons[str(e.get('decision_reason'))]=reasons.get(str(e.get('decision_reason')),0)+1
    print(f'   distinct decision_reason strings = {len(reasons)} over {len(adjs)} decisions')
    # reviewed_audio_sha256 present?
    sha_missing=sum(1 for e in adjs.values() if not e.get('reviewed_audio_sha256'))
    print(f'   decisions WITHOUT reviewed_audio_sha256: {sha_missing}')
