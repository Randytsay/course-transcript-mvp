"""Non-mutating Golden V2 historical SRT/TXT review against held acoustic gaps.

No provider, no Production ledger, no Drive client, no audio judgment. This
module deliberately does not reuse the job-local immutable staging operation.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_reference_companion import MAX_REFERENCE_BYTES

_SHA = re.compile(r'^[0-9a-f]{64}$')
_JOB = re.compile(r'^v2p_[0-9a-f]{32}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{10,200}$')
_TIMECODE = re.compile(
    r'^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*'
    r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$')
_ROLES = {'BASE_SRT', 'REVIEWED_SRT', 'REFERENCE_TXT'}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bytes(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise V2Conflict('missing, oversized or unsafe historical reference')
    raw = path.read_bytes()
    if len(raw) > max_bytes or not raw or b'\x00' in raw:
        raise V2Conflict('reference bytes invalid')
    return raw


def _time(parts: tuple[str, ...]) -> int:
    hh, mm, ss, ms = (int(value) for value in parts)
    if mm >= 60 or ss >= 60:
        raise V2Conflict('invalid reference timecode')
    return ((hh * 60 + mm) * 60 + ss) * 1000 + ms


def _cues(raw: bytes) -> list[tuple[int, int, str]]:
    text = raw.decode('utf-8-sig').replace('\r\n', '\n')
    cues: list[tuple[int, int, str]] = []
    for block in re.split(r'\n\s*\n', text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        matches = [i for i, line in enumerate(lines) if _TIMECODE.fullmatch(line)]
        if not matches:
            continue
        if len(matches) != 1:
            raise V2Conflict('ambiguous SRT cue timecode')
        ix = matches[0]; match = _TIMECODE.fullmatch(lines[ix]); assert match
        start, end = _time(match.groups()[:4]), _time(match.groups()[4:])
        if end <= start or not lines[ix + 1:]:
            raise V2Conflict('empty or invalid SRT cue')
        cues.append((start, end, ' '.join(lines[ix + 1:])))
    if not cues:
        raise V2Conflict('no parseable SRT cues')
    if any(b[0] < a[0] for a, b in zip(cues, cues[1:])):
        raise V2Conflict('SRT cue order is not monotonic')
    return cues


def _normalized(text: str) -> str:
    return ''.join(c.casefold() for c in unicodedata.normalize('NFKC', text)
                   if c.isalnum())


def _txt_anchor_hint(source_text: str, candidate_text: str) -> dict[str, Any]:
    """Weak, explicitly NON-acoustic overlap with an untimed edited TXT.

    Coarse fixed-size text anchors are intentionally not confidence scores:
    an edited TXT can omit repetitions or paraphrase genuine spoken phrases.
    """
    normalized = _normalized(candidate_text)
    source = _normalized(source_text)
    anchors = [normalized[i:i + 8] for i in range(0, len(normalized) - 7, 8)]
    return {'anchor_units': 8, 'anchors_examined': len(anchors),
            'anchors_found': sum(anchor in source for anchor in anchors),
            'source_has_timestamps': False, 'acoustic_proof': False}


def preview_historical_references(*, plan: dict, gap_manifest: dict,
                                  expected_gap_manifest_sha256: str,
                                  gap_manifest_bytes: bytes,
                                  expected_job_id: str, expected_source_sha256: str,
                                  plan_directory: Path) -> dict[str, Any]:
    """Only interpret a separately authorized immutable snapshot, no writes.

    The CLI is responsible for saving the returned report in a new private
    location. Plan paths must be absolute and cannot be symlinked.
    """
    if (not _JOB.fullmatch(expected_job_id) or not _SHA.fullmatch(expected_source_sha256)
            or not _SHA.fullmatch(expected_gap_manifest_sha256)
            or _sha(gap_manifest_bytes) != expected_gap_manifest_sha256
            or plan.get('schema') != 'golden-v2-historical-reference-plan-v1'
            or gap_manifest.get('job_id') != expected_job_id
            or plan.get('job_id') != expected_job_id
            or gap_manifest.get('source_sha256') != expected_source_sha256
            or plan.get('source_sha256') != expected_source_sha256
            or not _SHA.fullmatch(str(gap_manifest.get('acoustic_report_sha256', '')))
            or gap_manifest.get('human_listened') is not False):
        raise V2Conflict('historical gap/plan identity or immutable source mismatch')
    items = gap_manifest.get('review_items')
    if (not isinstance(items, list) or not 1 <= len(items) <= 100
            or not all(isinstance(item, dict) for item in items)):
        raise V2Conflict('invalid acoustic review gap manifest')
    last_index = 0
    for item in items:
        if (type(item.get('index')) is not int or item['index'] <= last_index
                or type(item.get('gap_start_ms')) is not int
                or type(item.get('gap_end_ms')) is not int
                or not 0 <= item['gap_start_ms'] < item['gap_end_ms']
                or item.get('human_listened') is not False):
            raise V2Conflict('invalid or already reviewed acoustic interval')
        last_index = item['index']

    references = plan.get('references')
    if not isinstance(references, list) or not 1 <= len(references) <= 3:
        raise V2Conflict('historical references must be explicitly selected')
    if len({entry.get('role') for entry in references}) != len(references):
        raise V2Conflict('duplicate reference role')
    if len({entry.get('drive_file_id') for entry in references}) != len(references):
        raise V2Conflict('duplicate Drive reference ID')
    folders = {entry.get('drive_parent_id') for entry in references}
    if len(folders) != 1 or not all(isinstance(fid, str) and _ID.fullmatch(fid) for fid in folders):
        raise V2Conflict('mixed or invalid Drive reference parents')

    snapshots: dict[str, dict] = {}
    for entry in references:
        role = entry.get('role')
        if (role not in _ROLES or not _SHA.fullmatch(str(entry.get('sha256') or ''))
                or not _ID.fullmatch(str(entry.get('drive_file_id') or ''))
                or not str(entry.get('drive_version') or '').strip()):
            raise V2Conflict('historical reference provenance incomplete')
        local = Path(str(entry.get('local_path') or ''))
        if not local.is_absolute() or local.is_symlink() or local.resolve().parent == plan_directory.resolve():
            # Source copies belong to a separate trusted, read-only evidence root.
            raise V2Conflict('historical reference path is not isolated')
        if local.suffix.lower() not in ({'.txt', '.bin'} if role == 'REFERENCE_TXT' else {'.srt', '.bin'}):
            raise V2Conflict('reference role and file extension disagree')
        raw = _bytes(local, max_bytes=MAX_REFERENCE_BYTES)
        if _sha(raw) != entry['sha256']:
            raise V2Conflict('historical reference bytes differ from pinned SHA')
        try:
            decoded = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise V2Conflict('historical reference is not UTF-8') from exc
        snapshots[role] = {'sha256': entry['sha256'], 'drive_file_id': entry['drive_file_id'],
                           'drive_version': str(entry['drive_version']),
                           'drive_parent_id': entry['drive_parent_id'],
                           'cues': _cues(raw) if role.endswith('SRT') else None,
                           'text': decoded if role == 'REFERENCE_TXT' else None}
    rows = []
    counts = {'both_srt_text_present': 0, 'any_srt_timeline_empty': 0,
              'base_reviewed_text_disagreement': 0, 'human_listening_pending': len(items)}
    for item in items:
        start, end = item['gap_start_ms'], item['gap_end_ms']
        variants = {}
        for role, data in snapshots.items():
            if data['cues'] is None:
                continue
            overlapping = [t for s, e, t in data['cues'] if e > start and s < end]
            text = ' '.join(overlapping)
            variants[role] = {'overlapping_cue_count': len(overlapping),
                              'text_sha256': _sha(_normalized(text).encode()),
                              'excerpt_review_only': text[:160]}
        base = variants.get('BASE_SRT'); reviewed = variants.get('REVIEWED_SRT')
        if base and reviewed and base['overlapping_cue_count'] and reviewed['overlapping_cue_count']:
            counts['both_srt_text_present'] += 1
        if any(v['overlapping_cue_count'] == 0 for v in variants.values()):
            counts['any_srt_timeline_empty'] += 1
        disagreement = bool(base and reviewed and base['text_sha256'] != reviewed['text_sha256'])
        if disagreement:
            counts['base_reviewed_text_disagreement'] += 1
        txt = snapshots.get('REFERENCE_TXT')
        txt_hints = ({role: _txt_anchor_hint(txt['text'], values['excerpt_review_only'])
                      for role, values in variants.items()} if txt else {})
        rows.append({'index': item['index'], 'gap_start_ms': start,
                     'gap_end_ms': end, 'acoustic_classification': item.get('disposition'),
                     'srt': variants, 'text_variant_disagreement': disagreement,
                     'reference_txt_available': 'REFERENCE_TXT' in snapshots,
                     'reference_txt_anchor_hints': txt_hints,
                     'audio_review_status': 'PENDING',
                     'reference_is_acoustic_evidence': False,
                     'auto_replacement_allowed': False})
    return {'schema': 'golden-v2-historical-reference-preview-v1',
            'job_id': expected_job_id, 'source_sha256': expected_source_sha256,
            'gap_manifest_sha256': expected_gap_manifest_sha256,
            'acoustic_report_sha256': gap_manifest['acoustic_report_sha256'],
            'references': [{key: data[key] for key in ('sha256', 'drive_file_id',
                            'drive_version', 'drive_parent_id')} | {'role': role}
                           for role, data in sorted(snapshots.items())],
            'counts': counts, 'gaps': rows,
            'human_listening_attested': False, 'golden_qa_pass': False,
            'provider_calls_started': False, 'production_ledger_modified': False,
            'drive_writes_started': False, 'production_delivery_allowed': False}
