"""Provider-free read of already-ledger-attested Gemini/Chirp comparison.

No model calls, corrections, invented timestamps or Golden semantic PASS.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_versioned_artifacts import resolve_versioned


def read_source_bound_comparison(*, job_dir: Path, job_id: str,
                                 source_sha256: str, stages: list[dict]) -> dict | None:
    stage=next((x for x in stages if x.get('name')=='compare'),None)
    if not stage or stage.get('state') not in {'COMPLETE','REVIEW_REQUIRED'}:
        return None
    digest=stage.get('evidence_sha')
    if not isinstance(digest,str) or len(digest)!=64:
        raise V2Conflict('comparison stage lacks immutable SHA')
    path=resolve_versioned(Path(job_dir)/'golden-v2'/'engine-comparison.json',digest)
    if path.stat().st_size>8_000_000:
        raise V2Conflict('oversized comparison evidence')
    comparison=json.loads(path.read_bytes())
    if (comparison.get('kind')!='golden_v2_primary_chirp_comparison'
            or comparison.get('job_id')!=job_id
            or comparison.get('source_sha256')!=source_sha256
            or comparison.get('chirp_timing_authority') is not True
            or comparison.get('auto_publish_authorized') is not False
            or comparison.get('comparison_reference_kind') not in {'GEMINI_PRIMARY','HUMAN_CORRECTED_SRT'}
            or not isinstance(comparison.get('candidates'),list)
            or comparison.get('pending_review_count')!=len(comparison['candidates'])
            or len(comparison['candidates'])>2000):
        raise V2Conflict('untrusted or stale semantic evidence')
    items=[]
    for idx,entry in enumerate(comparison['candidates']):
        if not isinstance(entry,dict) or entry.get('comparison_status')!='REVIEW_REQUIRED':
            raise V2Conflict('comparison candidate evidence invalid')
        start=entry.get('source_time_start');end=entry.get('source_time_end')
        if start is not None and (type(start) is not int or type(end) is not int or start<0 or end<=start):
            raise V2Conflict('invalid Chirp times in semantic candidate')
        items.append({'candidate_index':idx,'kind':entry.get('kind','TEXT_DISAGREEMENT'),
             'source_time_start_ms':start,'source_time_end_ms':end,
             'chirp_excerpt':str(entry.get('chirp_normalized_excerpt') or '')[:120],
             'reference_excerpt':str(entry.get('gemini_normalized_excerpt') or '')[:120],
             'decision':'ADVISORY_UNRESOLVED'})
    return {'comparison_evidence_sha256':digest,
            'comparison_reference_kind':comparison['comparison_reference_kind'],
            'semantic_review_items':items,
            'semantic_status':'NO_CONFLICTS_IN_COMPARISON' if not items else 'REVIEW_ITEMS_AVAILABLE',
            'model_invoked_now':False,'new_paid_usage':False,
            'semantic_correctness_certified':False}
