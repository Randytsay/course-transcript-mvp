"""Are ALL 4,091 windows the same mechanical formula, using adjacent context
for untimed candidates? If so, the honest gate is one rule, not two.
"""
import json
from pathlib import Path
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
tot_mech=0; tot=0
for lbl,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    G=V2/jid/'golden-v2'
    c=json.loads((G/'engine-comparison.json').read_text())
    a=json.loads((G/'adjudications.json').read_text())
    adjs={e['candidate_index']:e for e in a.get('adjudications',[])}
    mech=0; n=0; timed_mech=0; untimed_mech=0
    for i,cd in enumerate(c['candidates']):
        e=adjs.get(i)
        if not e: continue
        w=e.get('reviewed_audio_window_ms')
        if not (isinstance(w,list) and len(w)==2): continue
        n+=1
        s,t=cd.get('source_time_start'),cd.get('source_time_end')
        if s is not None and t is not None:
            anchor=(s,t); kind='timed'
        else:
            ctx=cd.get('adjacent_chirp_context') or {}
            l,r=ctx.get('left_chirp_word_ms'),ctx.get('right_chirp_word_ms')
            if l is None or r is None: continue
            anchor=(l,r); kind='untimed'
        if w==[anchor[0]-1000,anchor[1]+1000]:
            mech+=1
            if kind=='timed': timed_mech+=1
            else: untimed_mech+=1
    print(f'{lbl}: decisions_with_window={n} mechanical={mech} ({100*mech/max(1,n):.1f}%) '
          f'[timed_mech={timed_mech} untimed_mech={untimed_mech}]')
    tot+=n; tot_mech+=mech
print(f'ALL THREE: {tot_mech}/{tot} = {100*tot_mech/max(1,tot):.1f}% mechanical')
