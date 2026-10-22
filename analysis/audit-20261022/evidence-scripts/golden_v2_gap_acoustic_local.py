"""FREE, local-only verification of the 251018 and 251025 gaps.

No paid provider call. Uses the repo's own acoustic QA helpers (ffmpeg +
local VAD) to decide whether each large gap is genuine silence or dropped
speech. This is the input to the Phase C decision: if they are silence, no
re-transcription money is needed for them.
"""
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, '/srv/ai-workspace/projects/golden-v2-fix-20261021')
from app.providers.golden_v2_acoustic_qa import _peak_db, _vad_assessment

V2 = Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
JOBS = {
    '251018': ('v2p_560465ab6db05e3da593837f4d76c630', 13254075),
    '251025': ('v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be', 8883670),
}
for label, (jid, dur_ms) in JOBS.items():
    J = V2 / jid
    G = J / 'golden-v2'
    m = json.loads(next(G.glob('chirp-merged-candidate*.json')).read_text())
    words = m.get('words') or []
    gaps = []
    for a, b in zip(words, words[1:]):
        g = b['start_ms'] - a['end_ms']
        if g > 15000:
            gaps.append((a['end_ms'], b['start_ms'], g, a['word'], b['word']))
    print('=' * 74)
    print(f'{label}: {len(gaps)} gaps > 15s (audio {dur_ms/1000:.1f}s)')
    for g0, g1, g, wa, wb in gaps:
        # sample the middle of the gap in 5s slices
        slices = []
        s = g0
        while s < g1:
            e = min(g1, s + 5000)
            try:
                vad = _vad_assessment(J, s, e) or {}
            except Exception as exc:
                vad = {"error": type(exc).__name__}
            try:
                peak = _peak_db(J, s, e)
            except Exception as exc:
                peak = f"ERR:{type(exc).__name__}"
            slices.append((s, e, vad, peak))
            s = e
        voiced = sum(1 for _, _, v, _ in slices
                     if isinstance(v, dict) and v.get("speech_detected") is True)
        print(f'  gap {g0/1000:.1f}-{g1/1000:.1f}s ({g}ms) {wa!r}->{wb!r}  '
              f'slices={len(slices)} speech_detected_slices={voiced}')
        for s, e, v, peak in slices[:12]:
            sd = v.get('speech_detected') if isinstance(v, dict) else None
            extra = ''
            if isinstance(v, dict):
                for k in ('speech_ratio', 'rms_db', 'mean_db', 'vad_speech_ratio'):
                    if k in v:
                        extra += f' {k}={v[k]}'
            print(f'      {s/1000:8.1f}-{e/1000:8.1f}s speech={sd} peak_db={peak}{extra}')
        if len(slices) > 12:
            print(f'      ... {len(slices)-12} more slices')
    print()
