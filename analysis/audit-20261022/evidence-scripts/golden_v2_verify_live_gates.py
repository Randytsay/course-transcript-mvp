"""End-to-end proof inside the LIVE running container: the deployed gates
refuse the three real audited production batches, and the deployed code
is the merged SHA's code.
"""
import json
import subprocess
from pathlib import Path

JOBS = {
    '251025': 'v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be',
    '251018': 'v2p_560465ab6db05e3da593837f4d76c5732ca7',
    '261017': 'v2p_62a124bdd4595aca91c53085c3732ca7',
}
JOBS['251018'] = 'v2p_560465ab6db05e3da593837f4d76c630'
SCRIPT = r'''
import json, sys
from pathlib import Path
from app.pipeline.golden_v2_alignment_health import assess_alignment_health
from app.pipeline.golden_v2_review_evidence import assess_review_evidence, ReviewEvidenceError
V2 = Path('/app/data/golden-v2/jobs')
for label, jid in %s.items():
    G = V2 / jid / 'golden-v2'
    c = json.loads((G / 'engine-comparison.json').read_text())
    a = json.loads((G / 'adjudications.json').read_text())
    p = json.loads((G / 'primary-stage.json').read_text())
    ev = p.get('window_evidence') or []
    cov = 0.0; span = 0.0
    for e in ev:
        w = (e.get('identity') or {}).get('window') or {}
        s, en = w.get('start_seconds'), w.get('end_seconds')
        if not isinstance(s, (int, float)) or not isinstance(en, (int, float)): continue
        span = max(span, en * 1000.0)
        if e.get('finish_reason') == 'STOP': cov += (en - s) * 1000.0
    merged = json.loads((G / 'chirp-merged.json').read_text())
    dur = merged.get('duration_ms')
    ratio = cov / dur if dur else None
    al = assess_alignment_health(candidates=c.get('candidates') or [], comparison=c,
                                 primary_coverage_ratio=ratio, duration_ms=dur)
    try:
        assess_review_evidence(candidates=c.get('candidates') or [],
                               decisions=a.get('adjudications') or [])
        rev = 'PASS(BAD)'
    except ReviewEvidenceError as exc:
        rev = 'BLOCKED'
    print(json.dumps({'job': label, 'coverage': round(ratio, 4) if ratio else None,
                      'alignment': al['status'], 'alignment_reasons': al['reasons'],
                      'review_evidence': rev}))
''' % repr(JOBS)

proc = subprocess.run(
    ["sudo", "docker", "exec", "-i", "golden-v2-chat-api-1", "python", "-c", SCRIPT],
    capture_output=True, text=True, timeout=300)
print("=== gates running INSIDE the live container against real production data")
for line in proc.stdout.splitlines():
    if line.strip().startswith("{"):
        d = json.loads(line)
        print(f"  {d['job']}: coverage={d['coverage']} alignment={d['alignment']} "
              f"{d['alignment_reasons']} review_evidence={d['review_evidence']}")
if proc.stderr.strip():
    print("stderr:", proc.stderr.strip()[:600])
print()
print("=== deployed code identity")
ident = subprocess.run(
    ["sudo", "docker", "exec", "golden-v2-chat-api-1", "python", "-c",
     "import os,hashlib;"
     "p='/app/app/pipeline/golden_v2_alignment_health.py';"
     "print('sha256', hashlib.sha256(open(p,'rb').read()).hexdigest()[:16]);"
     "print('release_sha_env', os.environ.get('COURSE_TRANSCRIPT_V2_RELEASE_SHA'))"],
    capture_output=True, text=True, timeout=120)
print(ident.stdout.strip())
