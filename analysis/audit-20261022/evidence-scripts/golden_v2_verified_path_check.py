"""Does the verified code path accept the hand-written reviewed-primary files?

If verify_primary_overlap_artifacts() rejects them, they were NOT produced by
the audited finalize_reviewed_primary_overlaps() path.
"""
import json,hashlib
from pathlib import Path
import sys
sys.path.insert(0,'/srv/ai-workspace/projects/golden-v2-fix-20261021')
from app.pipeline.golden_v2_primary_stage import verify_primary_overlap_artifacts
from app.jobs.golden_v2_ledger import V2Conflict
ROOT=Path('/input/jobs')
CASES=[('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be','efe79aa12e7b720c516c1587da51ec306f74b5f368bb8d8503f498952f396b49','64facacfbb8ec489f53d37448b551feb6a6e2ecfbcc56d9231861c9a118c99e3'),
       ('251018','v2p_560465ab6db05e3da593837f4d76c630','a68b15c997f51bba2da54d04cbebe3602a2bc6a4032d277d46b00da585e890a7','a89e0c3ef13f86f7c2e1048899807d3738774fb8119df67f61753335ae85b936')]
for label,jid,reviewed_sha,original_sha in CASES:
    j=ROOT/jid
    # stage_runner binds the reviewed file by ledger SHA
    print('='*72); print(label)
    try:
        report,records=verify_primary_overlap_artifacts(job_dir=j,job_id=jid,source_sha=report_sha if False else None,original_sha=original_sha) if False else (None,None)
    except Exception:
        pass
    # Need the real source sha
    import sqlite3
    c=sqlite3.connect('file:/input/ledger.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
    src=c.execute('select source_sha from v2_jobs where id=?',(jid,)).fetchone()['source_sha']
    try:
        verify_primary_overlap_artifacts(job_dir=j,job_id=jid,source_sha=src,original_sha=original_sha)
        print('  VERIFIED_PATH: ACCEPTED  <-- would mean audited path produced it')
    except V2Conflict as e:
        print('  VERIFIED_PATH: REJECTED ->',e)
    except Exception as e:
        print('  VERIFIED_PATH: ERROR',type(e).__name__,e)
    # what the reviewed file actually claims
    d=json.loads((j/'golden-v2'/f'primary-stage-reviewed-{reviewed_sha}.json').read_text())
    ar=d.get('audio_review') or {}
    ev=d.get('window_evidence') or []
    print('  claimed review_scope :',ar.get('review_scope'))
    print('  claimed resolution   :',str(ar.get('resolution'))[:150])
    print('  planned windows      :',len(d.get('windows') or []),' window_evidence:',len(ev))
    covered=sum(w['end_seconds']-w['start_seconds'] for w in (d.get('windows') or []) if w['start_seconds']<2700)
    total=(d.get('windows') or [])[-1]['end_seconds']
    print(f'  audio covered by reference text: {covered:.0f}s / {total:.0f}s = {100*covered/total:.1f}%')
