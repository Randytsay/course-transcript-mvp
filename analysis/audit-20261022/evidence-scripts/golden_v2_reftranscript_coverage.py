"""Decisive test: does 261017 reference-transcript.md cover the WHOLE file,
including the region the Gemini primary truncated (2690-5390s) and the tail?

Method: take Chirp word sequences at several time anchors and look them up
in the reference text. If present across the file, the reference is complete
and the paid re-run can be replaced by re-binding this artifact.
"""
import json,re,hashlib
from pathlib import Path
j=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
rt=(j/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace')
def norm(s): return re.sub(r'[\s\u3000]','',s)
nrt=norm(rt)
print('reference norm chars',len(nrt))
# chirp merged candidate words
cand=None
for p in sorted((j/'golden-v2').glob('chirp-merged-candidate*.json')):
    cand=p
m=json.loads(cand.read_text())
words=m.get('words') or []
print('chirp words',len(words),'duration_ms',m.get('duration_ms'))
def window_text(t0,t1,n=12):
    seg=[w for w in words if t0<=w['start_ms']<=t1]
    return ''.join(w['word'] for w in seg[:n])
anchors=[(0,120000,'0-120s'),(2690000,2900000,'2690-2900s (TRUNCATED REGION)'),
         (5000000,5200000,'5000-5200s (TRUNCATED REGION)'),
         (5380000,5500000,'5380-5500s'),(8000000,8100000,'8000-8100s'),
         (10700000,10781820,'10700-10781s (TAIL)')]
ok=0
for t0,t1,label in anchors:
    seg=norm(window_text(t0,t1))
    if not seg: print(f'  {label}: NO CHIRP WORDS'); continue
    probe=seg[:10]
    hit=nrt.find(probe)
    print(f'  {label}: chirp="{seg[:26]}"')
    print(f'      probe10={"FOUND at char "+str(hit) if hit>=0 else "NOT FOUND"}')
    if hit>=0: ok+=1
print()
print(f'reference contains chirp anchors at {ok}/{len(anchors)} sampled regions')
# Is the whole primary assembled text contained in the reference?
p=json.loads((j/'golden-v2/primary-stage.json').read_text())
nas=norm((p.get('assembled') or {}).get('text',''))
print('primary assembled norm chars',len(nas))
# sample many positions of the primary and see if each is present in ref
import random
random.seed(7)
positions=[int(len(nas)*f) for f in (0.0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9)]
hits=0
for pos in positions:
    probe=nas[pos:pos+12]
    if len(probe)<8: continue
    if nrt.find(probe)>=0: hits+=1
    else: print(f'   primary@{pos} NOT in reference: {probe!r}')
print(f'primary snippets found in reference: {hits}/{len(positions)}')
