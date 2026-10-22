"""1) Fuzzy coverage of 261017 reference-transcript.md vs Chirp across the file.
2) Provenance: where did it come from, and do equivalents exist for 251025/251018?
"""
import json,re,hashlib,difflib,os
from pathlib import Path
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
JID='v2p_62a124bdd4595aca91c53085c3732ca7'
j=V2/JID
rt=(j/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace')
def norm(s): return re.sub(r'[\s\u3000]','',s)
def alpha(s): return re.sub(r'[^一-鿿]','',s)
nrt=norm(rt); art=alpha(rt)
print('ref norm',len(nrt),'alpha',len(art))
m=json.loads(next((j/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
def seg(t0,t1,n=14):
    picked=[w['word'] for w in words if t0<=w['start_ms']<=t1][:n]
    return ''.join(picked)
print('--- fuzzy containment of chirp segments in reference (alpha-only, ratio>=0.80):')
for t0,t1,label in [(0,120000,'0-120s'),(2690000,2900000,'2690-2900s TRUNC-REGION'),
                     (5000000,5200000,'5000-5200s TRUNC-REGION'),(5380000,5500000,'5380-5500s'),
                     (8000000,8100000,'8000-8100s'),(10700000,10781820,'10700-10781s TAIL')]:
    s=alpha(seg(t0,t1))
    if len(s)<6: print(f'  {label}: no chirp words'); continue
    best=0.0; where=-1
    # slide over reference in 400-char strides
    for off in range(0,max(1,len(art)-len(s)),400):
        w=art[off:off+len(s)]
        r=difflib.SequenceMatcher(None,s,w).quick_ratio()
        if r>best: best=r; where=off
        if best>0.95: break
    print(f'  {label}: "{s[:24]}" best_ratio={best:.3f} at {where}')
print()
print('--- provenance hunt for reference-transcript.md (sha b04b5247...)')
target=hashlib.sha256((j/'golden-v2/reference-transcript.md').read_bytes()).hexdigest()
print('target sha',target)
roots=[Path('/opt/course-transcript-source/data'),Path('/opt/course-transcript-v2-chat/data'),
       Path('/srv/ai-workspace/projects'),Path('/srv/ai-workspace/tmp')]
for r in roots:
    if not r.exists(): continue
    for p in r.rglob('reference-transcript.md'):
        try: h=hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception: continue
        if h==target: print('   MATCH',p)
print('--- all reference-transcript.md on disk (path + size + job label):')
for r in roots:
    if not r.exists(): continue
    for p in r.rglob('reference-transcript.md'):
        lab=[x for x in ('251025','251018','261017') if x in str(p)]
        print(f'   {p.stat().st_size:>9} {lab} {p}')
