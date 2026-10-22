"""Quantify Gemini character-offset drift caused by the truncated window."""
import json
from pathlib import Path
C=json.loads(Path('/srv/ai-workspace/tmp/golden-v2-261017-pilot-20261021/comparison.current.json').read_text())
timed=[c for c in C['candidates'] if c.get('source_time_start') is not None]
def drift(c):
    s=c.get('source_time_start')
    g=c.get('gemini_char_range')
    ch=c.get('chirp_char_range')
    if s is None or not g or not ch: return None
    return (s,g[0]-ch[0])
rows=[d for d in (drift(c) for c in timed) if d]
rows.sort()
def stats(lo,hi,label):
    sel=[d for d in rows if lo<=d[0]<hi]
    if not sel: return None
    ds=[d[1] for d in sel]
    return {'region':label,'n':len(sel),'drift_min':min(ds),'drift_max':max(ds),
            'drift_median':sorted(ds)[len(ds)//2],'drift_last':ds[-1]}
out=[]
for lo,hi,label in [(0,2690000,'BEFORE 0-2690s'),(2690000,5390000,'INSIDE 2690-5390s'),
                     (5390000,8000000,'AFTER 5390-8000s'),(8000000,10781820,'AFTER 8000s-end')]:
    s=stats(lo,hi,label)
    if s: out.append(s)
# The decisive number: Gemini chars missing vs what a full window should hold.
last_inside=[d for d in rows if d[0]<5390000][-1]
first_after=[d for d in rows if d[0]>=5390000][0]
print(json.dumps({'buckets':out,
 'drift_at_end_of_truncated_window':last_inside,
 'drift_just_after_truncated_window':first_after,
 'char_offset_lost_across_truncation':last_inside[1]-first_after[1],
 'interpretation':'Gemini assembled text is missing N characters in the 2690-5390s window; every later comparison is misaligned by that amount, manufacturing false candidates.'},ensure_ascii=False,indent=2))
