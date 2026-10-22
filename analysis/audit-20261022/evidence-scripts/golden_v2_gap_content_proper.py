"""PROPER test (my earlier chunky probe had a stride bug):
Using one full-string SequenceMatcher, map reference blocks to Chirp char
positions -> time. Then ask, for each Chirp gap: does the reference have
substantial text between the matched anchors (=> Chirp omitted real content),
or does it jump too (=> the gap is genuine nonspeech)?
"""
import json,re,difflib
from pathlib import Path
def alpha(s): return re.sub(r'[^一-鿿]','',s)
J=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
ref=alpha((J/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace'))
m=json.loads(next((J/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
ct=alpha(''.join(w['word'] for w in words))
# char index -> time map
cmap=[];pos=0
for w in words:
    L=len(alpha(w['word'])); cmap.append((pos,pos+L,w['start_ms'],w['end_ms'])); pos+=L
def t_at(ci):
    for a,b,s,e in cmap:
        if a<=ci<b: return s
    return cmap[-1][3] if cmap else 0
sm=difflib.SequenceMatcher(None,ref,ct,autojunk=False)
blocks=[b for b in sm.get_matching_blocks() if b.size>=6]
print(f'ref={len(ref)} chirp={len(ct)} ratio={sm.ratio():.4f} blocks>=6: {len(blocks)}')
# Chirp gaps
gaps=[]
for a,b in zip(words,words[1:]):
    g=b['start_ms']-a['end_ms']
    if g>10000: gaps.append((a['end_ms'],b['start_ms'],g,a['word'],b['word']))
# chirp char index boundaries for each gap (convert ms -> char index)
def ci_at_ms(t):
    for a,b,s,e in cmap:
        if s<=t<=e: return a
    # nearest word start
    for a,b,s,e in cmap:
        if s>t: return a
    return len(ct)
gaps=[]
for a,b in zip(words,words[1:]):
    g=b['start_ms']-a['end_ms']
    if g>10000: gaps.append((a['end_ms'],b['start_ms'],g,a['word'],b['word']))
# for each gap: find the matched block just before and just after in chirp coords
print()
for g0,g1,g,wa,wb in gaps:
    c0=ci_at_ms(g0); c1=ci_at_ms(g1)
    before=[bl for bl in blocks if bl.b+bl.size<=c0]
    after=[bl for bl in blocks if bl.b>=c1]
    lb=before[-1] if before else None
    fa=after[0] if after else None
    if lb is None or fa is None:
        print(f'gap {g}ms at {g0}-{g1}: anchor missing (lb={lb is not None}, fa={fa is not None})'); continue
    ref_span=fa.a-lb.a-lb.size
    chirp_span=fa.b-lb.b-lb.size
    print(f'gap {g}ms at {g0/1000:.1f}-{g1/1000:.1f}s  ({wa!r} -> {wb!r})')
    print(f'    unmatched REFERENCE chars between anchors: {ref_span}')
    print(f'    unmatched CHIRP chars between anchors   : {chirp_span}')
    if ref_span>40:
        print(f'    => reference has {ref_span} chars where Chirp has {chirp_span}: {ref[lb.a+lb.size:lb.a+lb.size+70]!r}')
    else:
        print('    => reference ALSO jumps here (no independent content) => gap is genuine nonspeech')
