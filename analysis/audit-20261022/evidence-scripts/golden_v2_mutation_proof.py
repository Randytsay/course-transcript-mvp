"""Verify: was the 'immutable' primary-stage.json mutated in place for 261017?
And does the audited verifier accept the bound 261017 primary?"""
import json,hashlib,sqlite3,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,'/srv/ai-workspace/projects/golden-v2-fix-20261021')
from app.pipeline.golden_v2_primary_stage import verify_primary_overlap_artifacts
from app.jobs.golden_v2_ledger import V2Conflict
ROOT=Path('/input/jobs')
c=sqlite3.connect('file:/input/ledger.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
for label,jid in [('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    j=ROOT/jid
    p=j/'golden-v2/primary-stage.json'
    st=p.stat()
    d=json.loads(p.read_text())
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    led=c.execute("select evidence_sha from v2_stages where job_id=? and name='gemini_primary'",(jid,)).fetchone()['evidence_sha']
    print(f'=== {label} primary-stage.json')
    print(f'   file_sha256      = {sha}')
    print(f'   ledger_sha       = {led}')
    print(f'   match            = {sha==led}')
    print(f'   mtime            = {datetime.fromtimestamp(st.st_mtime,timezone.utc).isoformat()}')
    print(f'   top ready_for_chirp      = {d.get("ready_for_chirp")}')
    print(f'   assembled.ready_for_chirp_comparison = {(d.get("assembled") or {}).get("ready_for_chirp_comparison")}')
    print(f'   CONTRADICTION (in-place mutation) = {d.get("ready_for_chirp") is True and (d.get("assembled") or {}).get("ready_for_chirp_comparison") is False}')
    print(f'   audio_review present = {bool(d.get("audio_review"))}  (execute_approved_primary_stage never writes this key)')
    # raw audit mtimes for comparison
    raws=sorted((j/'gemini-primary').glob('*.json'),key=lambda x:x.stat().st_mtime)
    for r in raws:
        rr=json.loads(r.read_text())
        print(f'   raw {datetime.fromtimestamp(r.stat().st_mtime,timezone.utc).isoformat()} finish={rr.get("finish_reason")} chars={len(rr.get("text","") or "")} {r.name[:16]}')
    # audited verifier
    src=c.execute('select source_sha from v2_jobs where id=?',(jid,)).fetchone()['source_sha']
    try:
        verify_primary_overlap_artifacts(job_dir=j,job_id=jid,source_sha=src,original_sha=sha)
        print('   VERIFIED_PATH: ACCEPTED')
    except V2Conflict as e:
        print('   VERIFIED_PATH: REJECTED ->',e)
    except Exception as e:
        print('   VERIFIED_PATH: ERROR',type(e).__name__,e)
    # is the ledger sha the ORIGINAL (pre-mutation) report?
    orig=c.execute("select prior_evidence_sha,reviewer,review_reference from v2_review_resumptions where job_id=? and stage_name='gemini_primary'",(jid,)).fetchall()
    for r in orig: print('   RESUMPTION',json.dumps(dict(r),ensure_ascii=False))
