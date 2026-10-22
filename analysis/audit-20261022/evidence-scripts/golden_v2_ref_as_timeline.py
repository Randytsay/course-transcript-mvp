"""DECISIVE for the recommendation: can 逐字稿.md (human full transcript, 0 cents)
replace the truncated Gemini window for 261017?

Test: align reference->Chirp with exact matched blocks, build a char->time
map, and ask:
 1. What % of reference chars get a time anchor?
 2. Does the resulting timeline span the whole 10,781.82s?
 3. In the Gemini-truncated region 2690-5390s, does the reference carry content
    that Chirp does NOT have (=> real Chirp omissions the reference can reveal)?
"""
import json,re,difflib
from pathlib import Path
def alpha(s): return re.sub(r'[^一-鿿]','',s)
J=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
ref=alpha((J/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace'))
m=json.loads(next((J/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
ct=alpha(''.join(w['word'] for w in words))
# chirp char index -> time
cmap=[];pos=0
for w in words:
    L=len(alpha(w['word'])); cmap.append((pos,pos+L,w['start_ms'],w['end_ms'])); pos+=L
def t_of(ci):
    lo,hi=0,len(cmap)-1
    while lo<=hi:
        mid=(lo+hi)//2
        a,b,s,e=cmap[mid]
        if ci<a: hi=mid-1
        elif ci>=b: lo=mid+1
        else: return s
    return cmap[min(lo,len(cmap)-1)][2]
sm=difflib.SequenceMatcher(None,ref,ct,autojunk=False)
blocks=[b for b in sm.get_matching_blocks() if b.size>=8]
print(f'ref={len(ref)} chirp={len(ct)} ratio={sm.ratio():.4f} anchored blocks={len(blocks)}')
anchored=sum(b.size for b in blocks)
print(f'1) reference chars directly anchored by exact Chirp match: {anchored} ({100*anchored/len(ref):.1f}%)')
times=[t_of(b.b) for b in blocks]
print(f'2) anchored timeline spans {min(times)/1000:.1f}s .. {max(times)/1000:.1f}s of 10781.82s '
      f'-> {100*(max(times)-min(times))/10781820:.1f}% of the file')
# region analysis 2690-5390
REG=(2690000,5390000)
inreg=[b for b in blocks if REG[0]<=t_of(b.b)<=REG[1]]
print(f'3) matched blocks inside 2690-5390s: {len(inreg)} covering {sum(b.size for b in inreg)} chars')
# reference content in region NOT present in chirp (deletions from ref side)
smr=difflib.SequenceMatcher(None,ref,ct,autojunk=False)
# reference chars inside the region's time span that are unmatched -> use time map on ref via anchors
# Build ref->time by anchoring blocks, then locate reference spans whose time falls in region
def ref_time(ri):
    # last anchored block at or before ri
    lo=0;hi=len(blocks)-1;res=None
    while lo<=hi:
        mid=(lo+hi)//2
        if blocks[mid].a<=ri: res=blocks[mid];lo=mid+1
        else: hi=mid-1
    if res is None: return 0
    # linear interpolation within the block
    off=ri-res.a
    return t_of(res.b)+ (off/res.size)*(t_of(res.b+res.size-1)-t_of(res.b))
region_ref_chars=sum(1 for i in range(0,len(ref),7) if REG[0]<=ref_time(i)<=REG[1])
print(f'   reference chars whose interpolated time is inside the region: ~{region_ref_chars*7} (sampled)')
# unmatched reference spans inside region (potential Chirp omissions)
unmatched=[]
for tag,i1,i2,j1,j2 in smr.get_opcodes():
    if tag in ('delete','replace') and i2-i1>=6:
        t=ref_time(i1)
        if REG[0]<=t<=REG[1]: unmatched.append((t,i2-i1,ref[i1:i2]))
print(f'   UNMATCHED reference spans inside region (>=6 chars) = {len(unmatched)}, total {sum(u[1] for u in unmatched)} chars')
for t,sz,s in unmatched[:6]:
    print(f'      t~{t/1000:.1f}s ({sz} chars) {s[:56]!r}')
