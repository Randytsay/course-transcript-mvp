"""Verify 261017's tail corruption: Chirp vs audio probe vs 逐字稿.md.
Three-source comparison of the 迴向 liturgy at the end of the lecture.
"""
import json,re
from pathlib import Path
def alpha(s): return re.sub(r'[^一-鿿]','',s)
J=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
m=json.loads(next((J/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
ct=alpha(''.join(w['word'] for w in words))
ref=alpha((J/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace'))
probe=alpha(json.loads(next(Path('/srv/ai-workspace/tmp/golden-v2-261017-pilot-20261021/probe-records/responses').glob('*.json')).read_text())['response_text'])
print('=== 261017 tail, three independent sources ===')
print('\n[1] CHIRP (published subtitle text):')
print('   ',ct[-230:])
print('\n[2] REAL AUDIO PROBE (Gemini 3.8 Flash on original audio, 10700-10765s):')
print('   ',probe[-200:])
print('\n[3] 逐字稿.md (human-edited Drive reference):')
print('   ',ref[-210:])
print()
print('=== duplication check in Chirp tail (each phrase should appear once) ===')
for ph in ('迴向彌勒淨土','福慧增長','以此功德','供養三世一切諸佛','供養三世一切菩薩','彌勒尊佛','往生佛國','不退增長','地藏菩薩'):
    print(f'   Chirp x{ct.count(ph):<2} {ph!r}   probe x{probe.count(ph):<2}   ref x{ref.count(ph):<2}')
print()
print('=== divergence: what does Chirp say where the others say otherwise? ===')
for wrong,right in [('不退增長','福慧增長'),('願捐獻','但願三世一切眾生蒙佛接引'),('佛皆引','蒙佛接引'),('是僧願以經法會功德','至誠願以今夜法會功德'),('回向彌勒淨土','迴向彌勒淨土')]:
    print(f'   Chirp {wrong!r} x{ct.count(wrong)} | probe {right!r} x{probe.count(right)} | ref {right!r} x{ref.count(right)}')
