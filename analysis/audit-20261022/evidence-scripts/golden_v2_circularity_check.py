"""Is 261017's 疑義審查報告.md (full-length, 02:59:34) Chirp-derived?
If its text matches Chirp words in the truncated region, it is CIRCULAR as a
reference for detecting Chirp omissions.
"""
import re,json,hashlib
from pathlib import Path
rep=Path('/srv/ai-workspace/tmp/refcheck/261017__疑義審查報告.md').read_text(encoding='utf-8',errors='replace')
# parse SRT-like cues
cues=re.findall(r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)',rep,flags=re.S)
print('parsed cues',len(cues))
def ms(t):
    h,m,rest=t.split(':');s,mm=rest.split(',')
    return ((int(h)*60+int(m))*60+int(s))*1000+int(mm)
def norm(s): return re.sub(r'[^一-鿿]','',s)
# chirp merged
j=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
m=json.loads(next((j/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
def chirp_between(a,b): return ''.join(w['word'] for w in words if a<=w['start_ms']<=b)
# Region: truncated window 2690-5390s
print()
print('=== region 2690-5390s (the truncated Gemini window) ===')
reg=[c for c in cues if ms(c[0])>=2690000 and ms(c[1])<=5390000]
print('cues in region:',len(reg),'-> text chars',len(norm(''.join(c[2] for c in reg))))
if reg:
    rtext=norm(''.join(c[2] for c in reg))
    ctext=norm(chirp_between(2690000,5390000))
    print('chirp chars in region:',len(ctext))
    # is chirp text a subsequence / prefix of report text?
    print('chirp[:40] =',ctext[:40])
    print('rep  [:40] =',rtext[:40])
    print('chirp found inside report text:',ctext[:40] in rtext)
    # longest common prefix
    lo=0
    for a,b in zip(rtext,ctext):
        if a!=b: break
        lo+=1
    print('longest common prefix (chars):',lo,'of chirp',len(ctext))
    import difflib
    print('similarity ratio:',round(difflib.SequenceMatcher(None,rtext[:2000],ctext[:2000]).quick_ratio(),3))
print()
print('=== whole-file: does report text == chirp text? ===')
allrep=norm(''.join(c[2] for c in cues))
allchirp=norm(''.join(w['word'] for w in words))
print('report chars',len(allrep),'chirp chars',len(allchirp))
lo=0
for a,b in zip(allrep,allchirp):
    if a!=b: break
    lo+=1
print('longest common prefix over whole file:',lo)
print('report[:60] :',allrep[:60])
print('chirp [:60] :',allchirp[:60])
