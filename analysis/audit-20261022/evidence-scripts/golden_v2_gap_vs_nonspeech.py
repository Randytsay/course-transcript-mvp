"""CORRECTION CHECK: is Chirp's 35.28s gap actually speech, or non-speech?

My own paid Gemini audio probe covered 10,700,000-10,765,000 ms and reported a
nonspeech interval. Compare it against Chirp's gap windows for 261017.
"""
import json,re
from pathlib import Path
J=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
# saved probe response
resp=list((Path('/srv/ai-workspace/tmp/golden-v2-261017-pilot-20261021/probe-records/responses')).glob('*.json'))
d=json.loads(resp[0].read_text())
raw=d.get('raw_response') or d
txt=json.dumps(raw,ensure_ascii=False)
print('=== probe record: response id',d.get('response_id'),'reviewed range',d.get('reviewed_source_range_ms'))
# find nonspeech intervals in the stored review payload
def walk(o):
    if isinstance(o,dict):
        if 'nonspeech_intervals' in o: yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
p=list(walk(d))
print('=== speech/nonspeech intervals reported by the audio model (relative to clip start 10700.000s)')
for obj in p[:1]:
    for si in obj.get('speech_intervals',[]):
        print(f'   SPEECH    {si["start_seconds"]:>8.3f} - {si["end_seconds"]:>8.3f}  (abs {10700+si["start_seconds"]:.3f}-{10700+si["end_seconds"]:.3f}s)  {si.get("text","")[:36]!r}')
    for si in obj.get('nonspeech_intervals',[]):
        print(f'   NONSPEECH {si["start_seconds"]:>8.3f} - {si["end_seconds"]:>8.3f}  (abs {10700+si["start_seconds"]:.3f}-{10700+si["end_seconds"]:.3f}s)  {si.get("description","")}')
print()
print('=== Chirp gaps for 261017 (ms)')
m=json.loads(next((J/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
words=m.get('words') or []
for a,b in zip(words,words[1:]):
    g=b['start_ms']-a['end_ms']
    if g>10000:
        print(f'   gap {a["end_ms"]}-{b["start_ms"]} ({g}ms)  after {a["word"]!r} before {b["word"]!r}')
        print(f'      -> overlaps probe NONSPEECH 10704.908-10742.840 ? {not (a["end_ms"]>10742840 or b["start_ms"]<10704908)}')
