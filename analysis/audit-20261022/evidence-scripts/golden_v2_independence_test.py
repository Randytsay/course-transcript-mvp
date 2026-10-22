"""DECISIVE test for 261017 逐字稿.md: is it INDEPENDENT of Chirp?

If the reference has content in regions where Chirp has gaps (Chirp-derived
text would inherit Chirp's gaps), it is an independent human transcript and
can serve as a genuine third-party arbiter at zero cost.
"""
import json,re,difflib
from pathlib import Path
J=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
ref=(J/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace')
def alpha(s): return re.sub(r'[^一-鿿]','',s)
art=alpha(ref)
m=json.loads(next((J/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
# Chirp gaps
gaps=[]
for a,b in zip(words,words[1:]):
    g=b['start_ms']-a['end_ms']
    if g>10000: gaps.append((g,a,b))
print('Chirp gaps >10s:',len(gaps))
for g,a,b in gaps:
    print(f'  gap {g}ms  after {a["word"]!r}({a["end_ms"]}) before {b["word"]!r}({b["start_ms"]})')
print()
# Build reference char->approx position map by locating successive Chirp words
# Use a sliding search for each anchor pair with a FINE stride (8 chars).
def find_best(probe):
    if len(probe)<6: return -1,0.0
    best=(-1,0.0)
    n=len(art)
    step=4
    for off in range(0,max(1,n-len(probe)),step):
        w=art[off:off+len(probe)]
        if w==probe: return off,1.0
        r=difflib.SequenceMatcher(None,probe,w).quick_ratio()
        if r>best[1]:
            best=(off,r)
            if r>0.97: break
    return best
print('=== For each Chirp gap: does 逐字稿.md contain text BETWEEN the anchors?')
for g,a,b in gaps:
    left=alpha(''.join(w['word'] for w in words if a['end_ms']-8000<=w['start_ms']<=a['end_ms']))
    right=alpha(''.join(w['word'] for w in words if b['start_ms']<=w['start_ms']<=b['start_ms']+8000))
    lo,rl=find_best(left[-12:] if len(left)>=12 else left)
    ro,rr=find_best(right[:12] if len(right)>=12 else right)
    print(f'  gap {g}ms: left_anchor@{lo}(r={rl:.2f}) right_anchor@{ro}(r={rr:.2f})')
    if lo>=0 and ro>=0:
        if ro>lo:
            between=art[lo+len(left[-12:]):ro]
            print(f'      reference text in gap = {len(between)} chars: {between[:120]}')
        else:
            print('      reference jumps with Chirp (no independent content in gap)')
    else:
        print('      anchor not located in reference')
print()
print('=== control: does 逐字稿.md contain the region text Chirp HAS at 8000-8100s?')
seg=alpha(''.join(w['word'] for w in words if 8000000<=w['start_ms']<=8100000)[:24])
o,r=find_best(seg)
print(f'  8000-8100s probe found at {o} ratio {r:.2f} (probe={seg[:30]})')
