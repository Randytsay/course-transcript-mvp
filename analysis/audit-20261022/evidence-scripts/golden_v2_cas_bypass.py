"""Distinguish CAS-with-lying-verifier (251025/251018) from out-of-band write (261017)."""
import json,sqlite3
from datetime import datetime,timezone
p='/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True);c.row_factory=sqlite3.Row
for label,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                  ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                  ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    print('='*70); print(label)
    r=c.execute("select * from v2_review_resumptions where job_id=? and stage_name='gemini_primary'",(jid,)).fetchall()
    print(f'  gemini_primary resumptions: {len(r)}')
    for x in r: print('   ',json.dumps(dict(x),ensure_ascii=False))
    for s in c.execute("select name,state,estimate_cents,actual_cents,updated_at,evidence_sha from v2_stages where job_id=? and name in ('gemini_primary','adjudication','golden_qa')",(jid,)):
        print('   stage',s['name'],s['state'],'est',s['estimate_cents'],'act',s['actual_cents'],'updated',s['updated_at'])
    j=c.execute('select revision,status,approved_cents from v2_jobs where id=?',(jid,)).fetchone()
    print('   job rev',j['revision'],j['status'],'approved',j['approved_cents'])
    # reconciliation rows (evidence of a legit CAS)
    rc=c.execute("select * from v2_reconciliations where job_id=?",(jid,)).fetchall()
    print(f'   reconciliations: {len(rc)}')
