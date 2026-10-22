"""DECISIVE: does 逐字稿.md contain the text Chirp dropped in its 35.28s gap?

Chirp gap: 10,704,760 -> 10,740,040 ms (35,280 ms).
Real audio probe (my own paid Gemini audio call, 10,700,000-10,765,000 ms)
HEARD: 為所得到的，卻是他人千生萬生得不到。迴向，弟子陳美輝 ... 至誠願以今夜法會功德，
迴向彌勒淨土，福慧增長，以此功德，供養三世一切諸佛
If 逐字稿.md contains that block, the gap is NOT silence - it is real speech
that Chirp dropped, and an independent transcript exists to arbitrate it.
"""
import json,re
from pathlib import Path
J=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
def alpha(s): return re.sub(r'[^一-鿿]','',s)
ref=alpha((J/'golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace'))
m=json.loads(next((J/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
GAP=(10704760,10740040)
print('=== A) Chirp content around the 35.28s gap')
before=[w for w in words if w['end_ms']<=GAP[0]][-8:]
after=[w for w in words if w['start_ms']>=GAP[1]][:8]
print('  words before gap:', ''.join(w['word'] for w in before))
print('  words after  gap:', ''.join(w['word'] for w in after))
print('  -> Chirp text spanning the gap: NONE (no word starts inside 10704.8-10740.0s)')
inside=[w for w in words if GAP[0]<=w['start_ms']<=GAP[1]]
print('  words inside gap window:',len(inside))
print()
print('=== B) does 逐字稿.md contain the 迴向 block the gap swallowed?')
for probe in ('迴向彌勒淨土','福慧增長','供養三世一切諸佛','千生萬生得不到'):
    i=ref.find(probe)
    print(f'  {probe!r}: idx={i}' + (f'  ...{ref[max(0,i-60):i+70]}...' if i>=0 else '  NOT FOUND'))
print()
print('=== C) position of that block relative to the reference end')
i=ref.find('迴向彌勒淨土')
print(f'  reference alpha length={len(ref)}; 迴向 block at {i} (i.e. {100*i/len(ref):.1f}% into the file)')
print()
print('=== D) Chirp: does it contain the 迴向 block anywhere?')
ct=alpha(''.join(w['word'] for w in words))
for probe in ('迴向','彌勒淨土','福慧增長','供養三世一切諸佛'):
    print(f'  Chirp contains {probe!r}: {ct.count(probe)}')
print()
print('=== E) VERDICT INPUTS: reference tail vs Chirp tail')
print('  reference tail (last 260 alpha):', ref[-260:])
print('  chirp     tail (last 260 alpha):', ct[-260:])
