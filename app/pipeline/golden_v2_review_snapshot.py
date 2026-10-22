"""Read a held Chirp candidate's gap assessment, never certify audio or Golden.

The caller supplies one read-only Ledger snapshot. Candidate filenames are
hints only: the complete Ledger SHA must match the immutable file bytes.
Acoustic reports bind the canonical JSON digest, which can differ from the
Ledger's byte digest for an indented candidate. No mtime/latest selection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_versioned_artifacts import version_path


def _read_json(path: Path, *, limit: int) -> tuple[bytes, dict[str, Any]]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise V2Conflict('missing, unsafe or oversize Chirp review evidence')
    try:
        content = path.read_bytes()
        if len(content) > limit:
            raise V2Conflict('oversize Chirp review evidence')
        record = json.loads(content)
    except (OSError, ValueError, UnicodeError) as exc:
        raise V2Conflict('unreadable Chirp review evidence') from exc
    if not isinstance(record, dict):
        raise V2Conflict('invalid Chirp review evidence object')
    return content, record


def inspect_current_chirp_acoustic_review(
    *, snapshot: dict[str, Any], job_dir: Path,
) -> dict[str, Any]:
    """Select only a uniquely bound report for a REVIEW_REQUIRED Chirp stage.

    This view does not rehash audio, run VAD, inspect words acoustically,
    attest a reviewer, change a stage, or make a provider/Drive request.
    COMPLETE-stage Golden QA keeps its separate, QA-attested acoustic chain.
    """
    job = snapshot['job']
    job_id, source_sha = job['id'], job['source_sha']
    stage = next((s for s in snapshot['stages']
                  if s['name'] == 'chirp_independent'), None)
    result: dict[str, Any] = dict(
        status='UNAVAILABLE', job_id=job_id, source_sha256=source_sha,
        chirp_stage_state=stage['state'] if stage else None,
        evidence_scope='CHIRP_TIMELINE_GAP_ASSESSMENT_ONLY',
        golden_pass=False, human_listening_attested=False,
    )
    if not stage or stage['state'] != 'REVIEW_REQUIRED' or not stage.get('evidence_sha'):
        return {**result, 'reason': 'NO_LEDGER_BOUND_HELD_CHIRP_CANDIDATE'}
    job_dir = Path(job_dir)
    directory = job_dir/'golden-v2'
    if (not re.fullmatch(r'v2p_[0-9a-f]{32}', job_id)
            or not re.fullmatch(r'[0-9a-f]{64}', source_sha)
            or job_dir.name != job_id or job_dir.parent.is_symlink()
            or job_dir.is_symlink() or directory.is_symlink()
            or not directory.is_dir()):
        raise V2Conflict('unsafe or mismatched Chirp review directory/source')
    digest = stage['evidence_sha']
    canonical = directory/'chirp-merged.json'
    alternate = version_path(canonical, digest)  # validates the full SHA
    candidate = directory/f'chirp-merged-candidate-{digest[:20]}.json'
    selected = None
    for path in (canonical, alternate, candidate):
        if path.is_symlink():
            raise V2Conflict('symlink in Chirp review evidence')
        if not path.exists():
            continue
        content, record = _read_json(path, limit=16*1024**2)
        if hashlib.sha256(content).hexdigest() == digest:
            selected = record
        elif path != canonical:
            raise V2Conflict('changed Ledger-bound Chirp candidate')
    if selected is None:
        raise V2Conflict('missing Ledger-bound Chirp candidate')
    if (selected.get('schema') != 'golden-v2-chirp-merged-v1'
            or selected.get('job_id') != job_id
            or selected.get('source_sha') != source_sha
            or selected.get('timing_authority') != 'chirp_3'):
        raise V2Conflict('Chirp candidate source/job binding conflict')
    merged_sha = hashlib.sha256(json.dumps(selected, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result.update(chirp_evidence_sha256=digest, merged_sha256=merged_sha)
    matches: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob('chirp-acoustic-qa-*.json')):
        content, report = _read_json(path, limit=2*1024**2)
        if report.get('merged_sha256') != merged_sha:
            continue  # A different immutable candidate is historical only.
        acoustic_sha = hashlib.sha256(content).hexdigest()
        if (report.get('source_sha256') != source_sha
                or report.get('schema') != 'golden-v2-acoustic-qa-v1'
                or report.get('status') not in {'PASS', 'REVIEW'}
                or path.name not in {
                    f'chirp-acoustic-qa-{acoustic_sha[:20]}.json',
                    f'chirp-acoustic-qa-{acoustic_sha}.json'}):
            raise V2Conflict('Chirp acoustic report source/hash binding conflict')
        matches[acoustic_sha] = report
    if not matches:
        return {**result, 'reason': 'NO_CURRENT_CHIRP_ACOUSTIC_REPORT'}
    if len(matches) != 1:
        return {**result, 'status': 'AMBIGUOUS',
                'reason': 'MULTIPLE_CURRENT_CHIRP_ACOUSTIC_REPORTS',
                'candidate_acoustic_sha256s': sorted(matches)}
    acoustic_sha, report = next(iter(matches.items()))
    return {**result, 'status': 'BOUND_CURRENT',
            'acoustic_evidence_sha256': acoustic_sha, 'acoustic_report': report}
