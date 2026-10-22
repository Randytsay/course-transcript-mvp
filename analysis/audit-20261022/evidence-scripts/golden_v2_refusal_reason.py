"""Why did Gemini refuse 251025/251018 window 1? Would a re-run hit the same block?"""
import json,hashlib
from pathlib import Path
ROOT=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')
def slot(identity):
    s={k:identity[k] for k in ('model','source_sha256','window')}
    return hashlib.sha256(json.dumps(s,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
for label,jid in [('251025','v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be'),
                  ('251018','v2p_560465ab6db05e3da593837f4d76c630'),
                  ('261017','v2p_62a124bdd4595aca91c53085c3732ca7')]:
    j=ROOT/jid
    print('='*72); print(label)
    for p in sorted((j/'golden-v2').glob('primary-stage*.json')):
        d=json.loads(p.read_text())
        for item in (d.get('window_evidence') or []):
            ident=item['identity']
            w=ident['window']
            if not (2690 <= w['start_seconds'] < 5390): continue
            rp=j/'gemini-primary'/f'{slot(ident)}.json'
            if not rp.exists(): continue
            raw=json.loads(rp.read_text())
            print(f"  window {w['start_seconds']}-{w['end_seconds']}s")
            print(f"    status={raw.get('status')} finish={raw.get('finish_reason')!r} text_len={len(raw.get('text','') or '')}")
            print(f"    human_review_required={raw.get('human_review_required')}")
            rr=raw.get('raw_response') or {}
            pf=rr.get('prompt_feedback') or {}
            print(f"    prompt_feedback={json.dumps(pf,ensure_ascii=False)[:300]}")
            for cand in (rr.get('candidates') or [])[:1]:
                print(f"    candidate.finish_reason={cand.get('finish_reason')} safety_ratings={json.dumps(cand.get('safety_ratings'),ensure_ascii=False)[:300]}")
                print(f"    candidate.parts_len={len(cand.get('content',{}).get('parts',[]) or [])}")
            u=raw.get('usage_metadata') or {}
            print(f"    usage prompt={u.get('prompt_token_count')} out={u.get('candidates_token_count')} think={u.get('thoughts_token_count')}")
            break
        break
