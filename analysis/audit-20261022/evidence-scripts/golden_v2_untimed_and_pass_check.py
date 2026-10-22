"""1) What reviewed_audio_window_ms did untimed candidates receive?
2) Why does an acoustic-qa file say PASS while unexamined_gap_count>0?
"""
import json,collections
from pathlib import Path
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
for label,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                  ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                  ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    j=ROOT/jid
    d=json.loads((j/'golden-v2/adjudications.json').read_text())
    comp=json.loads((j/'golden-v2/engine-comparison.json').read_text())
    cands=comp['candidates']
    untimed_windows=collections.Counter(); timed_windows=collections.Counter()
    for a in d['adjudications']:
        i=a['candidate_index']; c=cands[i]; w=a.get('reviewed_audio_window_ms')
        if c.get('source_time_start') is None: untimed_windows[json.dumps(w)]+=1
        else: timed_windows[json.dumps(w)]+=1
    print(f'== {label} adjudication_count={len(d["adjudications"])} comparison_candidates={len(cands)}')
    print(f'   untimed candidates in comparison: {sum(1 for c in cands if c.get("source_time_start") is None)}')
    print(f'   distinct reviewed windows for UNTIMED candidates: {len(untimed_windows)}')
    for w,n in untimed_windows.most_common(4): print(f'      {w} x{n}')
    print(f'   distinct reviewed windows for TIMED candidates: {len(timed_windows)}')
    # acoustic PASS contradiction
    for p in sorted((j/'golden-v2').glob('*acoustic-qa*.json')):
        try: q=json.loads(p.read_text())
        except Exception: continue
        if q.get('status')=='PASS' and (q.get('unexamined_gap_count') or 0)>0:
            print(f'   !! {p.name}: status=PASS but unexamined_gap_count={q.get("unexamined_gap_count")}')
            print('      keys:',list(q)[:14])
            gaps=q.get('gaps') or q.get('unexamined_gaps') or []
            print('      sample gap:',json.dumps(gaps[:1],ensure_ascii=False)[:400])
            break
    print()
