"""What mechanical-window ratio should the threshold use?

Measure the combined ratio (own span, or adjacent context for untimed) and
the timed-only ratio for each real job, so the threshold is evidence-based
and still leaves room for a genuine review.
"""
import json
from pathlib import Path
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
for lbl,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    G=V2/jid/'golden-v2'
    c=json.loads((G/'engine-comparison.json').read_text())
    a=json.loads((G/'adjudications.json').read_text())
    adjs={e['candidate_index']:e for e in a.get('adjudications',[])}
    comb=0; comb_n=0; timed_m=0; timed_n=0
    for i,cd in enumerate(c['candidates']):
        e=adjs.get(i)
        if not e: continue
        w=e.get('reviewed_audio_window_ms')
        if not (isinstance(w,list) and len(w)==2): continue
        s,t=cd.get('source_time_start'),cd.get('source_time_end')
        if s is not None and t is not None:
            anchor=(s,t); timed_n+=1
        else:
            ctx=cd.get('adjacent_chirp_context') or {}
            l,r=ctx.get('left_chirp_word_ms'),ctx.get('right_chirp_word_ms')
            if l is None or r is None: continue
            anchor=(l,r)
        comb_n+=1
        if w==[anchor[0]-1000,anchor[1]+1000]:
            comb+=1
            if s is not None: timed_m+=1
    print(f'{lbl}: combined_mech={comb}/{comb_n} ({100*comb/max(1,comb_n):.1f}%)  '
          f'timed_only_mech={timed_m}/{timed_n} ({100*timed_m/max(1,timed_n):.1f}%)')
