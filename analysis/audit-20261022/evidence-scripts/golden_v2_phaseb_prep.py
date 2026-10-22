"""Phase B preparation for 261017 (read-only, no paid call).

1. Confirm the 261017 primary's damaged window boundaries from the ledger.
2. Recompute the exact sub-window plan for the 2690-5390s replacement.
3. Verify the clip SHA and duration for each planned sub-window.
4. Confirm the 261017 job's approved/unspent budget headroom.
"""
import json, hashlib, subprocess
from pathlib import Path
J = Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7')
G = J / 'golden-v2'
p = json.loads((G / 'primary-stage.json').read_text())
ev = p.get('window_evidence') or []
print('=== 261017 primary window evidence')
for e in ev:
    w = (e.get('identity') or {}).get('window') or {}
    print(f"  idx={w.get('index')} {w.get('start_seconds')}-{w.get('end_seconds')}s "
          f"finish_reason={e.get('finish_reason')!r} "
          f"raw_audit={str(e.get('raw_audit_sha256'))[:12]}")
print()
bad = [e for e in ev if e.get('finish_reason') != 'STOP']
for e in bad:
    w = (e.get('identity') or {}).get('window') or {}
    print(f"  DAMAGED WINDOW: {w.get('start_seconds')}-{w.get('end_seconds')}s "
          f"reason={e.get('finish_reason')}")
print()
# sub-window plan: cover the damaged span with overlap
s, en = 2690.0, 5390.0
PLAN = [(2690.0, 3590.0), (3580.0, 4480.0), (4470.0, 5370.0), (5360.0, 5390.0)]
print('=== replacement sub-window plan (10s overlap, <=900s each)')
total_s = 0
for a, b in PLAN:
    total_s += (b - a)
    print(f"  {a:.1f}-{b:.1f}s  ({(b-a):.0f}s)")
print(f"  total audio to re-transcribe: {total_s:.0f}s")
print()
# budget headroom
import sqlite3
db = sqlite3.connect('file:/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite?mode=ro', uri=True)
db.row_factory = sqlite3.Row
job = db.execute("SELECT id,status,revision,approved_cents,estimated_cents FROM v2_jobs WHERE id=?",
                 ('v2p_62a124bdd4595aca91c53085c3732ca7',)).fetchone()
print('=== 261017 ledger job:', dict(job))
st = db.execute("SELECT name,state,estimate_cents,actual_cents FROM v2_stages WHERE job_id=?",
                ('v2p_62a124bdd4595aca91c53085c3732ca7',)).fetchall()
spent = 0
for r in st:
    print('   stage', dict(r))
    spent += int(r['actual_cents'] or 0)
print(f'  sum(actual_cents)={spent}  approved={job["approved_cents"]}')
gem = db.execute("SELECT name,state,estimate_cents,actual_cents FROM v2_stages WHERE job_id=? AND name='gemini_primary'",
                 ('v2p_62a124bdd4595aca91c53085c3732ca7',)).fetchone()
print(f'  gemini_primary: estimate={gem["estimate_cents"]} actual={gem["actual_cents"]} '
      f'-> unused within that stage = {int(gem["estimate_cents"]) - int(gem["actual_cents"] or 0)}')
print(f'  job-level headroom = approved - spent = {int(job["approved_cents"]) - spent}')
db.close()
print()
# source audio presence + sha
audio = J / 'normalized.flac'
print('=== source audio:', audio.name, audio.stat().st_size, 'bytes')
h = hashlib.sha256()
with audio.open('rb') as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b''):
        h.update(chunk)
print('  sha256:', h.hexdigest())
dur = subprocess.run(
    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
     '-of', 'default=noprint_wrappers=1:nokey=1', str(audio)],
    capture_output=True, text=True, timeout=60)
print('  ffprobe duration:', dur.stdout.strip(), dur.stderr.strip()[:200])
