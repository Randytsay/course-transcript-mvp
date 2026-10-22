"""Per-job: which primary file the ledger bound, and what text it actually covers."""
import json,hashlib
from pathlib import Path
JOBS={'251025':'v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be',
      '251018':'v2p_560465ab6db05e3da593837f4d76c630',
      '261017':'v2p_62a124bdd4595aca91c53085c3732ca7'}
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
import sqlite3
c=sqlite3.connect('file:/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
def slot(identity):
    s={k:identity[k] for k in ('model','source_sha256','window')}
    return hashlib.sha256(json.dumps(s,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
out={}
for label,jid in JOBS.items():
    j=ROOT/jid
    st=dict(c.execute("select evidence_sha,actual_cents,estimate_cents from v2_stages where job_id=? and name='gemini_primary'",(jid,)).fetchone())
    bound=None
    for p in sorted((j/'golden-v2').glob('primary-stage*.json')):
        h=hashlib.sha256(p.read_bytes()).hexdigest()
        if h==st['evidence_sha']: bound=p
    d=json.loads(bound.read_text()) if bound else {}
    ev=d.get('window_evidence') or []
    wins=d.get('windows') or []
    raws=[]
    for item in ev:
        ident=item['identity']
        p=j/'gemini-primary'/f'{slot(ident)}.json'
        raw=json.loads(p.read_text()) if p.exists() else {}
        u=raw.get('usage_metadata',{}) or {}
        raws.append({'idx':ident['window']['index'],'start_s':ident['window']['start_seconds'],'end_s':ident['window']['end_seconds'],
                     'finish':raw.get('finish_reason'),'status':raw.get('status'),'chars':len(raw.get('text','') or ''),
                     'out_tok':u.get('candidates_token_count'),'think_tok':u.get('thoughts_token_count')})
    # planned vs executed
    planned=[(w['index'],w['start_seconds'],w['end_seconds']) for w in wins]
    executed=[(r['idx'],r['start_s'],r['end_s']) for r in raws]
    covered_s=sum(r['end_s']-r['start_s'] for r in raws if r['chars']>0)
    total_s=planned[-1][2] if planned else 0
    out[label]={'job_id':jid,'ledger_primary_evidence_sha':st['evidence_sha'],'bound_file':bound.name if bound else None,
        'planned_windows':len(planned),'executed_windows':len(executed),
        'planned_span_s':[planned[0][1],planned[-1][2]] if planned else None,
        'total_audio_s':total_s,'audio_with_text_s':round(covered_s,1),
        'audio_without_text_s':round(total_s-covered_s,1),
        'coverage_pct':round(100*covered_s/total_s,1) if total_s else None,
        'assembled_chars':len((d.get('assembled') or {}).get('text','') or ''),
        'ready_for_chirp':d.get('ready_for_chirp'),
        'raws':raws,
        'gemini_actual_cents':st['actual_cents'],'gemini_estimate_cents':st['estimate_cents'],
        'unused_reservation_cents':st['estimate_cents']-(st['actual_cents'] or 0)}
print(json.dumps(out,ensure_ascii=False,indent=2))
