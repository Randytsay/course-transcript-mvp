"""Check all three Golden V2 jobs for the truncated-primary / offset-drift defect."""
import json,hashlib
from pathlib import Path
JOBS={'251025':'v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be',
      '251018':'v2p_560465ab6db05e3da593837f4d76c630',
      '261017':'v2p_62a124bdd4595aca91c53085c3732ca7'}
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
ANALYSIS=Path('/srv/ai-workspace/projects/golden-v2-fix-20261021/analysis/delivery-20261021T145350Z')
def slot(identity):
    s={k:identity[k] for k in ('model','source_sha256','window')}
    return hashlib.sha256(json.dumps(s,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
report={}
for label,jid in JOBS.items():
    j=ROOT/jid
    rep=json.loads((j/'golden-v2/primary-stage.json').read_text())
    wins=[]
    for item in rep['window_evidence']:
        ident=item['identity']
        p=j/'gemini-primary'/f'{slot(ident)}.json'
        raw=json.loads(p.read_text()) if p.exists() else {}
        u=raw.get('usage_metadata',{}) or {}
        wins.append({'index':ident['window']['index'],
                     'start_s':ident['window']['start_seconds'],'end_s':ident['window']['end_seconds'],
                     'finish':raw.get('finish_reason'),'status':raw.get('status'),
                     'chars':len(raw.get('text','') or ''),
                     'in_tok':u.get('prompt_token_count'),'out_tok':u.get('candidates_token_count'),
                     'think_tok':u.get('thoughts_token_count')})
    truncated=[w for w in wins if w['finish']!='STOP']
    # drift in the draft comparison
    drift=None
    for cf in (ANALYSIS/label/'comparison.current.json',ANALYSIS/label/'engine-comparison.json'):
        if cf.exists():
            C=json.loads(cf.read_text());break
    else:
        C=json.loads((Path('/srv/ai-workspace/tmp/golden-v2-261017-pilot-20261021/comparison.current.json')).read_text()) if label=='261017' else None
    if C:
        timed=[c for c in C['candidates'] if c.get('source_time_start') is not None and c.get('gemini_char_range') and c.get('chirp_char_range')]
        if timed:
            timed.sort(key=lambda c:c['source_time_start'])
            def off(c):return c['gemini_char_range'][0]-c['chirp_char_range'][0]
            offs=[off(c) for c in timed]
            drift={'n':len(timed),'first':offs[0],'last':offs[-1],'min':min(offs),'max':max(offs),
                   'median':sorted(offs)[len(offs)//2],
                   'late_median':sorted([o for c,o in zip(timed,offs) if c['source_time_start']>0.5*max(x['source_time_start'] for x in timed)])[max(1,len([o for c,o in zip(timed,offs) if c['source_time_start']>0.5*max(x['source_time_start'] for x in timed)])//2)] if timed else None}
    report[label]={'job_id':jid,'windows':wins,'truncated_windows':truncated,
                   'truncated_count':len(truncated),
                   'assembled_chars':len((rep.get('assembled') or {}).get('text','')),
                   'ready_for_chirp':rep.get('ready_for_chirp'),'drift':drift}
print(json.dumps(report,ensure_ascii=False,indent=2))
