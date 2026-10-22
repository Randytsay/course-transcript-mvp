"""Can reference-transcript.md replace the paid Gemini re-run for 261017?

Check: does it contain the text the truncated window lost, and can it be
mapped to source time? Also compare it to the mutated primary-stage.json.
"""
import json,hashlib,re
from pathlib import Path
j=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
rt=(j/'golden-v2/reference-transcript.md')
text=rt.read_text(encoding='utf-8',errors='replace')
print('reference-transcript.md: bytes=',rt.stat().st_size,'chars=',len(text),'lines=',text.count(chr(10))+1)
print('sha256=',hashlib.sha256(rt.read_bytes()).hexdigest())
# markdown headings often carry time markers
heads=re.findall(r'^.*$',text,flags=re.M)
timeish=[h for h in heads if re.search(r'\d{1,2}:\d{2}',h)]
print('lines containing mm:ss =',len(timeish))
for h in timeish[:8]: print('   ',h[:110])
# primary assembled text
p=json.loads((j/'golden-v2/primary-stage.json').read_text())
assembled=(p.get('assembled') or {}).get('text','')
print()
print('primary assembled chars=',len(assembled))
# normalized comparison
def norm(s): return re.sub(r'[\s\u3000]','',s)
nrt=norm(text); nas=norm(assembled)
print('normalized: ref=',len(nrt),'assembled=',len(nas))
# does ref contain the assembled text (i.e. is it a superset)?
if nas and nas in nrt:
    print('VERDICT: reference-transcript.md CONTAINS the full assembled text (superset)')
    print('   extra normalized chars =',len(nrt)-len(nas))
else:
    # longest common prefix
    lo=0
    for a,b in zip(nrt,nas):
        if a!=b: break
        lo+=1
    print('VERDICT: not a superset. common prefix =',lo,'of assembled',len(nas))
    print('   ref next 120 :',nrt[lo:lo+120])
    print('   asm next 120 :',nas[lo:lo+120])
# the truncated window lost ~5168 chars: look for a distinctive phrase
probe='迴向弟子'
print()
print('ref contains 迴向弟子 x',nrt.count(probe))
print('assembled contains 迴向弟子 x',nas.count(probe))
# section markers for the 2690-5390s region
for marker in ('2690','5390','45:','89:','90:'):
    print(f'   marker {marker!r} in ref:',nrt.count(marker))
