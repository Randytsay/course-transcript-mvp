"""What did the untimed candidates' windows look like, and can a window be
verified against the candidate's adjacent Chirp context?
"""
import json
from pathlib import Path
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
for lbl,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    G=V2/jid/'golden-v2'
    c=json.loads((G/'engine-comparison.json').read_text())
    a=json.loads((G/'adjudications.json').read_text())
    adjs={e['candidate_index']:e for e in a.get('adjudications',[])}
    print('='*70); print(lbl)
    shown=0
    for i,cd in enumerate(c['candidates']):
        if cd.get('source_time_start') is not None: continue
        e=adjs.get(i)
        if not e: continue
        ctx=cd.get('adjacent_chirp_context') or {}
        w=e.get('reviewed_audio_window_ms')
        left,right=ctx.get('left_chirp_word_ms'),ctx.get('right_chirp_word_ms')
        # is the window consistent with adjacent context?
        ok=None
        if left is not None and right is not None and isinstance(w,list) and len(w)==2:
            ok = (w[0] <= left and w[1] >= right)
        print(f'  idx={i} kind={cd.get("kind")} window={w} left={left} right={right} '
              f'covers_adjacent={ok}')
        shown+=1
        if shown>=6: break
    # aggregate
    consistent=0; inconsistent=0; noctx=0
    for i,cd in enumerate(c['candidates']):
        if cd.get('source_time_start') is not None: continue
        e=adjs.get(i)
        if not e: continue
        ctx=cd.get('adjacent_chirp_context') or {}
        w=e.get('reviewed_audio_window_ms')
        left,right=ctx.get('left_chirp_word_ms'),ctx.get('right_chirp_word_ms')
        if left is None or right is None: noctx+=1; continue
        if isinstance(w,list) and len(w)==2 and w[0]<=left and w[1]>=right: consistent+=1
        else: inconsistent+=1
    print(f'  untimed totals: covers_adjacent={consistent} does_not={inconsistent} no_context={noctx}')
