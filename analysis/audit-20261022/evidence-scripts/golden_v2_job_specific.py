"""251018 two long gaps + 35s stretched token; 251025 merge loss; Drive state."""
import json,hashlib,collections
from pathlib import Path
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
# --- 251018 gaps from the acoustic report bound to the ledger
j=ROOT/'v2p_560465ab6db05e3da593837f4d76c630'
for p in sorted((j/'golden-v2').glob('*acoustic-qa*.json')):
    d=json.loads(p.read_text())
    if d.get('status')!='REVIEW': continue
    gaps=d.get('gaps_examined') or []
    longg=[g for g in gaps if (g.get('end_ms',0)-g.get('start_ms',0))>30000]
    print('=== 251018 acoustic REVIEW',p.name,'gaps',len(gaps),'long(>30s)',len(longg))
    for g in longg[:6]:
        print('   ',json.dumps({k:v for k,v in g.items() if k in ('start_ms','end_ms','position','disposition','reason','audible_above_rms_threshold')},ensure_ascii=False))
    break
# --- 251018 stretched single word in merged chirp
for label,jid in [('251018','v2p_560465ab6db05e3da593837f4d76c630'),('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be')]:
    jj=ROOT/jid
    for cand in sorted((jj/'golden-v2').glob('chirp-merged*.json')):
        m=json.loads(cand.read_text())
        words=m.get('words') or []
        if not words: continue
        stretched=[w for w in words if (w.get('end_ms',0)-w.get('start_ms',0))>20000]
        print(f'\n=== {label} {cand.name}: words={len(words)} stretched>20s={len(stretched)}')
        for w in stretched[:5]:
            print('   ',json.dumps({k:v for k,v in w.items() if k in ('word','start_ms','end_ms')},ensure_ascii=False))
        # biggest gaps between consecutive words
        gaps=[]
        for a,b in zip(words,words[1:]):
            g=b['start_ms']-a['end_ms']
            if g>15000: gaps.append((g,a,b))
        gaps.sort(key=lambda t:-t[0])
        print(f'   inter-word gaps >15s: {len(gaps)}')
        for g,a,b in gaps[:4]:
            print(f'      gap={g}ms after {a.get("word")!r}({a.get("end_ms")}) before {b.get("word")!r}({b.get("start_ms")})')
        break
