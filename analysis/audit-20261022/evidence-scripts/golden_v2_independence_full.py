"""TWO decisive checks.

A) Is 逐字稿.md an INDEPENDENT human transcript? Align it to Chirp with a proper
   full-string SequenceMatcher (my earlier chunky probe had a stride bug).
   Measure: overall match ratio, and — critically — in the 2690-5390s region
   that Gemini truncated: does the reference have content Chirp does NOT have?
   (=> Chirp omission) and vice versa.

B) Where did 251025's published 逐字稿.txt come from? Compare it against every
   chirp-merged candidate for that job. If it matches an older machine merge,
   it is machine output (not a human reference); if it matches none, it is
   something else (possibly human).
"""
import json,re,difflib
from pathlib import Path
def alpha(s): return re.sub(r'[^一-鿿]','',s)
V2=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
J=V2/'v2p_62a124bdd4595aca91c53085c3732ca7'
ref=alpha((J/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace'))
m=json.loads(next((J/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
ct=alpha(''.join(w['word'] for w in words))
print('='*70); print('A) 逐字稿.md vs Chirp (full-string alignment)')
sm=difflib.SequenceMatcher(None,ref,ct,autojunk=False)
print(f'  ref alpha={len(ref)} chirp alpha={len(ct)} ratio={sm.ratio():.4f}')
matched=sum(b.size for b in sm.get_matching_blocks())
print(f'  matched chars={matched} ({100*matched/len(ref):.1f}% of reference, {100*matched/len(ct):.1f}% of chirp)')
# map chirp word index -> char position, so we can ask about time regions
pos=0; wpos=[]
for w in words:
    L=len(alpha(w['word'])); wpos.append((pos,pos+L,w)); pos+=L
def region_chars(t0,t1):
    return alpha(''.join(w['word'] for w in words if t0<=w['start_ms']<=t1))
REG=(2690000,5390000)
rc=region_chars(*REG)
print(f'\n  --- Gemini-truncated region {REG[0]/1000}-{REG[1]/1000}s ---')
print(f'  chirp alpha in region={len(rc)}')
smr=difflib.SequenceMatcher(None,ref,rc,autojunk=False)
mr=sum(b.size for b in smr.get_matching_blocks())
print(f'  matched into reference={mr} ({100*mr/max(1,len(rc)):.1f}% of region chirp)')
# reference content NOT in this region's chirp -> is there reference-only text here?
print('\n  reference blocks that match OUTSIDE the region (i.e. reference has extra):')
extra=0; samples=[]
for b in smr.get_matching_blocks():
    if b.size>=12:
        # locate this reference span's position; find which time region it maps to
        rstart,bstart=b.a,b.b
        # find nearest chirp word index for bstart
        idx=min(range(len(wpos)),key=lambda i:abs(wpos[i][0]-bstart))
        t=words[idx]['start_ms']
        if not (REG[0]<=t<=REG[1]):
            extra+=b.size
            if len(samples)<3: samples.append((t,ref[rstart:rstart+40]))
print(f'  matched chars whose counterpart is OUTSIDE region: {extra}')
for t,s in samples: print(f'    at t={t/1000:.1f}s  {s!r}')
print()
print('='*70); print('B) 251025 published 逐字稿.txt provenance')
J2=V2/'v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'
pub=alpha(Path('/srv/ai-workspace/tmp/refcheck/251025_逐字稿.txt').read_text(encoding='utf-8',errors='replace'))
print(f'  published alpha={len(pub)}')
best=None
for p in sorted((J2/'golden-v2').glob('chirp-merged*.json')):
    d=json.loads(p.read_text()); ws=d.get('words') or []
    if not ws: continue
    t=alpha(''.join(w['word'] for w in ws))
    r=difflib.SequenceMatcher(None,pub,t,autojunk=False).ratio()
    print(f'  vs {p.name[:44]} words={len(ws)} alpha={len(t)} ratio={r:.4f}')
    if best is None or r>best[1]: best=(p.name,r)
print(f'  best match: {best}')
