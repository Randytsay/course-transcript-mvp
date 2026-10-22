"""Provenance of the 'reviewed primary' files for 251025/251018.

Question: was the promotion done by the verified code path
(finalize_reviewed_primary_overlaps / complete_reviewed_primary), or did
a file with fewer window records than planned windows get promoted?
"""
import json,hashlib
from pathlib import Path
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
for label,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                  ('251018','v2p_560465ab6db05e3da593837f4d76c630')]:
    j=ROOT/jid
    print('='*70); print(label,jid)
    for p in sorted((j/'golden-v2').glob('primary-stage*.json')):
        d=json.loads(p.read_text())
        wins=d.get('windows') or []; ev=d.get('window_evidence') or []
        ar=d.get('audio_review') or {}
        st=p.stat()
        print(f'\n-- {p.name}')
        print(f'   file_sha256={hashlib.sha256(p.read_bytes()).hexdigest()}')
        print(f'   mtime={st.st_mtime} planned_windows={len(wins)} window_evidence={len(ev)}')
        print(f'   window_span={[w["start_seconds"] for w in wins]}..{[w["end_seconds"] for w in wins]}')
        print(f'   ready_for_chirp={d.get("ready_for_chirp")} assembled_chars={len((d.get("assembled") or {}).get("text","") or "")}')
        print(f'   audio_review={json.dumps(ar,ensure_ascii=False)[:600]}')
        if ar:
            print(f'   reviewed_seams={json.dumps((d.get("assembled") or {}).get("reviewed_seams"),ensure_ascii=False)[:800]}')
            print(f'   discarded_window_text={json.dumps((d.get("assembled") or {}).get("discarded_window_text"),ensure_ascii=False)[:400]}')
    # resumptions ledger
    import sqlite3
    c=sqlite3.connect('file:/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
    for r in c.execute('select * from v2_review_resumptions where job_id=?',(jid,)):
        print('\n   RESUMPTION',json.dumps(dict(r),ensure_ascii=False)[:400])
