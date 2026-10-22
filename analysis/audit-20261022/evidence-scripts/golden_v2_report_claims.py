"""Verify two claims from 疑義審查報告.md:
A) '現有參考文件交叉比對：已比對《逐字稿.md》' - was the human transcript used?
B) Row 4 shows '--:--:-- ~ --:--:--' with '****' - are there real comparison
   candidates with NO time range and NO chirp text? (the fabricated-window case)
C) 251025/251018 reports cover only 36/41 min - confirm truncation.
"""
import json,re
from pathlib import Path
C=json.loads(Path('/srv/ai-workspace/tmp/golden-v2-261017-pilot-20261021/comparison.current.json').read_text())
cands=C['candidates']
empty=[c for c in cands if c.get('chirp_normalized_excerpt','')=='' and c.get('gemini_char_range')]
print('=== A/B: candidates with EMPTY chirp excerpt (no acoustic text at all)')
print('  count:',len(empty))
for c in empty[:4]:
    print('   ',json.dumps({k:v for k,v in c.items() if k in ('kind','source_time_start','source_time_end','chirp_normalized_excerpt','gemini_normalized_excerpt','chirp_evidence_ids','adjacent_chirp_context')},ensure_ascii=False)[:400])
# untimed + empty chirp excerpt = worst case
both=[c for c in cands if c.get('source_time_start') is None and c.get('chirp_normalized_excerpt','')=='']
print('  untimed AND empty chirp excerpt:',len(both))
print()
print('=== C: 疑義審查報告 coverage per job (last time marker):')
for f,label in [('251025__疑義審查報告.md','251025'),('251018__疑義審查報告.md','251018'),('261017__疑義審查報告.md','261017')]:
    t=Path('/srv/ai-workspace/tmp/refcheck/'+f).read_text(encoding='utf-8',errors='replace')
    tm=re.findall(r'(\d{2}):(\d{2}):(\d{2})',t)
    if tm:
        last=max((int(a)*3600+int(b)*60+int(c)) for a,b,c in tm)
        print(f'  {label}: last time marker = {last//3600}h{(last%3600)//60}m{last%60}s  ({last}s)')
    for key in ('雙引擎交叉驗證','逐字稿.md','原樣輸出完整字幕','待檢核疑義數','辨識總字數'):
        mm=re.search(re.escape(key)+r'[^\n]*',t)
        if mm: print(f'      {mm.group(0)[:150]}')
