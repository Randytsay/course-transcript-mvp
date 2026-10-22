"""Provenance + coverage of reference-transcript.md (V1 pipeline?) for all three jobs."""
import json,hashlib,re,os
from pathlib import Path
V1=Path('/opt/course-transcript-source/data/jobs')
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
LABELS={'251025':'v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be',
        '251018':'v2p_560465ab6db05e3da593837f4d76c630',
        '261017':'v2p_62a124bdd4595aca91c53085c3732ca7'}
def norm(s): return re.sub(r'[\s\u3000]','',s)
print('=== V1 job dirs matching labels:')
if V1.is_dir():
    for d in sorted(V1.iterdir()):
        if d.is_dir() and any(l in d.name for l in LABELS):
            print(' ',d.name)
else:
    print('  V1 jobs dir missing:',V1)
for label,jid in LABELS.items():
    print('='*72); print(label)
    v2=V2/jid
    cands=[p for p in v2.rglob('reference-transcript.md')]
    for p in cands:
        t=p.read_text(encoding='utf-8',errors='replace')
        print(f'  V2 artifact: {p.relative_to(v2)} bytes={p.stat().st_size} norm_chars={len(norm(t))} lines={t.count(chr(10))+1}')
        print('   first 200:',t[:200].replace('\n',' | '))
    # look for the same labels in V1
    if V1.is_dir():
        for d in sorted(V1.iterdir()):
            if not d.is_dir() or label not in d.name: continue
            print('  V1 dir:',d.name)
            for f in sorted(d.rglob('*')):
                if f.is_file() and f.suffix in ('.md','.txt','.json') and f.stat().st_size<4_000_000:
                    print(f'    {f.stat().st_size:>9} {f.relative_to(d)}')
