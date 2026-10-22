"""Measure candidate signals from the REAL artifacts of all three jobs, so the
gates are designed against actual data rather than guesses.

For each job: primary-stage windows (coverage), comparison candidate
distribution (offset cascade), adjudication set (rubber-stamp signature).
"""
import json,statistics
from pathlib import Path
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
JOBS={'251025':'v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be',
      '251018':'v2p_560465ab6db05e3da593837f4d76c630',
      '261017':'v2p_62a124bdd4595aca91c53085c3732ca7'}
for label,jid in JOBS.items():
    J=V2/jid; G=J/'golden-v2'
    print('='*72); print(label)
    # primary stage windows
    ps=G/'primary-stage.json'
    if ps.exists():
        d=json.loads(ps.read_text())
        print('  primary-stage keys:',list(d))
        w=d.get('windows') or []
        print(f'  primary windows: {len(w)}')
        for x in w[:8]:
            print('     ',{k:x.get(k) for k in ('index','start_ms','end_ms','status','finish_reason','char_count','text_chars')})
    # comparison candidates
    for cp in sorted(G.glob('engine-comparison*.json')):
        c=json.loads(cp.read_text())
        cands=c.get('candidates') or []
        print(f'  comparison {cp.name}: candidates={len(cands)} '
              f'pending={c.get("pending_review_count")} ratio={c.get("similarity_ratio_not_accuracy")} '
              f'exact={c.get("exact_normalized_match")}')
        deltas=[]
        kinds={}
        for cd in cands:
            cr=cd.get('chirp_char_range') or [0,0]; gr=cd.get('gemini_char_range') or [0,0]
            deltas.append(gr[0]-cr[0]); kinds[cd.get('kind')]=kinds.get(cd.get('kind'),0)+1
        if deltas:
            med=statistics.median(deltas)
            print(f'     char-offset delta: median={med} min={min(deltas)} max={max(deltas)}')
            big=[d for d in deltas if abs(d)>500]
            print(f'     |delta|>500: {len(big)}/{len(deltas)} ({100*len(big)/len(deltas):.1f}%)')
        print('     kinds:',kinds)
        untimed=[cd for cd in cands if cd.get('source_time_start') is None]
        print(f'     untimed candidates: {len(untimed)}')
    # adjudications
    for ap in sorted(G.glob('adjudication*.json')):
        a=json.loads(ap.read_text())
        adjs=a.get('adjudications') or a.get('decisions') or []
        reasons={}; decisions={}; wins={}
        for e in adjs:
            r=str(e.get('decision_reason') or '')
            reasons[r]=reasons.get(r,0)+1
            decisions[e.get('decision')]=decisions.get(e.get('decision'),0)+1
            w=e.get('reviewed_audio_window_ms')
            if isinstance(w,list) and len(w)==2: wins[tuple(w)]=wins.get(tuple(w),0)+1
        print(f'  adjudication {ap.name}: n={len(adjs)} distinct_reasons={len(reasons)} '
              f'decisions={decisions} distinct_windows={len(wins)}')
        if reasons:
            top=max(reasons.items(),key=lambda kv:kv[1])
            print(f'     top reason x{top[1]}: {top[0][:70]!r}')
