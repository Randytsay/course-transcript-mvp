"""1) Diff the two 251025 reviewed-primary files (relabel test).
2) What did golden_qa certify, given the reference only covered 20-30%?
"""
import json,hashlib,sqlite3
from pathlib import Path
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
JID='v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'
j=ROOT/JID
a=j/'golden-v2/primary-stage-reviewed-71a8c12c9df8ce44443a13e0f5b914ad354cb06f7d32e427f253e2d3153ba362.json'
b=j/'golden-v2/primary-stage-reviewed-efe79aa12e7b720c516c1587da51ec306f74b5f368bb8d8503f498952f396b49.json'
A=json.loads(a.read_text());B=json.loads(b.read_text())
print('=== 251025 reviewed-primary diff (71a8c12c -> efe79aa1, ledger-bound) ===')
def flat(d,pre=''):
    out={}
    if isinstance(d,dict):
        for k,v in d.items(): out.update(flat(v,f'{pre}.{k}'))
    elif isinstance(d,list):
        out[pre]=json.dumps(d,ensure_ascii=False)
    else: out[pre]=d
    return out
fa,fb=flat(A),flat(B)
diffs=[k for k in set(fa)|set(fb) if fa.get(k)!=fb.get(k)]
for k in sorted(diffs):
    print(f'  {k}:')
    print(f'     71a8c12c = {str(fa.get(k))[:220]}')
    print(f'     efe79aa1 = {str(fb.get(k))[:220]}')
print(f'  total differing fields: {len(diffs)}')

print()
print('=== golden_qa evidence ===')
c=sqlite3.connect('file:/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
for label,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                  ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                  ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    jj=ROOT/jid
    row=c.execute("select evidence_sha,state,actual_cents from v2_stages where job_id=? and name='golden_qa'",(jid,)).fetchone()
    print(f'\n-- {label}: state={row["state"]} sha={row["evidence_sha"]}')
    for p in sorted((jj/'golden-v2').glob('*qa*.json'))+sorted((jj/'golden-v2').glob('golden-qa*.json')):
        try: d=json.loads(p.read_text())
        except Exception: continue
        print('   ',p.name,'->',json.dumps({k:v for k,v in d.items() if k in ('schema','status','semantic_gate','acoustic_gate','coverage','summary','result','verdict','checks','unexamined_gap_count')},ensure_ascii=False)[:900])
