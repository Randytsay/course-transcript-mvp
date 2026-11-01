"""Optional human transcript hints for Golden V2; never acoustic evidence.

A Drive reference is never added to paid provider inputs and never decides
Golden PASS.  Every selected file must be pinned to its Drive identity and
immutable local bytes; ambiguous associations fail closed.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.providers.golden_v2_compare import AlignmentNeedsReview, compare_gemini_chirp

REFERENCE_EXTENSIONS = frozenset({'.txt', '.srt'})
MAX_REFERENCE_BYTES = 2 * 1024 * 1024
_SUFFIX = re.compile(r'(?:參考|人工|校正|修正|完整版|原始|修訂|正式|校對|逐字稿|字幕|transcript|reference|corrected|final)+$', re.I)
_DATE = re.compile(r'(?:19|20)\d{6}')


def _key(name: str, *, reference: bool = False) -> str:
    stem = Path(name).stem
    if reference:
        for _ in range(4):
            updated = _SUFFIX.sub('', stem.strip(' _-（）()'))
            if updated == stem:
                break
            stem = updated
    return ''.join(c.casefold() for c in unicodedata.normalize('NFKC', stem)
                   if c.isalnum())


def _score(media_name: str, ref_name: str) -> int:
    media, ref = _key(media_name), _key(ref_name, reference=True)
    if not media or not ref:
        return 0
    if media == ref:
        return 3
    # A dated recording may have a topic-only reference title (or vice versa).
    if min(len(media), len(ref)) >= 3 and (ref in media or media in ref):
        return 2
    dates_a, dates_b = set(_DATE.findall(media)), set(_DATE.findall(ref))
    return 1 if dates_a and dates_a == dates_b else 0


def match_sibling_references(media: list[dict[str, Any]],
                             references: list[dict[str, Any]]) -> dict[str, dict]:
    """Match within the same immediate parent. Ties and shared names hold.

    A TXT+SRT pair may coexist, but two competing TXT or SRT versions must
    not be chosen by arbitrary listing order.
    """
    ranked: dict[str, list[tuple[int, dict]]] = {}
    for item in media:
        options = [(s, ref) for ref in references
                   if ref['parent_id'] == item['parent_id']
                   if (s := _score(item['name'], ref['name'])) > 0]
        best_by_format = {ext: max(s for s, ref in options if Path(ref['name']).suffix.lower() == ext)
                          for ext in {Path(ref['name']).suffix.lower() for _, ref in options}}
        ranked[item['file_id']] = [(s, ref) for s, ref in options
                                   if s == best_by_format[Path(ref['name']).suffix.lower()]]
    matched: dict[str, dict] = {}
    for item in media:
        choices = [ref for _, ref in ranked[item['file_id']]]
        if not choices:
            continue
        shared = any(any(ref['file_id'] == other['file_id'] for _, other in opts)
                     for key, opts in ranked.items() if key != item['file_id']
                     for ref in choices)
        extensions = [Path(ref['name']).suffix.lower() for ref in choices]
        if shared or len(extensions) != len(set(extensions)):
            matched[item['file_id']] = {'status': 'AMBIGUOUS', 'files': []}
        else:
            matched[item['file_id']] = {
                'status': 'MATCHED',
                'files': [{key: ref[key] for key in ('file_id', 'version', 'name',
                              'source_path', 'size_bytes', 'md5_checksum', 'parent_id')}
                          for ref in sorted(choices, key=lambda r: r['file_id'])],
            }
    return matched


def stage_reference_companions(*, job_dir: Path, job_id: str,
                               source_sha: str, references: dict | None,
                               fetch: Callable[[dict, Path], dict]) -> dict:
    """Locally snapshot already-discovered references, verified through GET.

    Caller must supply a private, source-bound job directory. No provider or
    Drive mutation and no paid stage is involved.
    """
    if not references:
        return {'status': 'NO_REFERENCE'}
    if references.get('status') != 'MATCHED':
        return {'status': 'AMBIGUOUS'}
    selections = references.get('files')
    if not isinstance(selections, list) or not 1 <= len(selections) <= 2:
        raise V2Conflict('invalid reference selection')
    root = Path(job_dir) / 'golden-v2'
    rows = []
    for i, candidate in enumerate(selections):
        extension = Path(candidate['name']).suffix.lower()
        if extension not in REFERENCE_EXTENSIONS or candidate['size_bytes'] > MAX_REFERENCE_BYTES:
            raise V2Conflict('reference format or size invalid')
        # The caller supplies a bounded GET-only fetcher and keeps temporary
        # files private; do not trust a filename or mime type as identity.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='ref-', dir=Path(job_dir)) as temp:
            target = Path(temp) / ('reference' + extension)
            observed = fetch(candidate, target)
            expected = {key: candidate[key] for key in ('file_id', 'version', 'source_path', 'size_bytes')}
            if any(observed.get(k) != value for k, value in expected.items()) or observed.get('downloaded_md5') != candidate['md5_checksum']:
                raise V2Conflict('reference Drive identity changed after preview')
            raw = target.read_bytes()
            if len(raw) != candidate['size_bytes'] or not raw or b'\x00' in raw:
                raise V2Conflict('reference text bytes invalid')
            try:
                raw.decode('utf-8-sig')
            except UnicodeDecodeError as exc:
                raise V2Conflict('reference is not UTF-8') from exc
            digest = hashlib.sha256(raw).hexdigest()
            if observed.get('downloaded_sha256') != digest:
                raise V2Conflict('reference SHA differs from Drive readback')
            filename = f'trusted-reference-{digest}{extension}'
            store_immutable_bytes(root / filename, raw)
            rows.append({**candidate, 'sha256': digest, 'local_filename': filename,
                         'format': extension[1:], 'acoustic_evidence': False})
    manifest = {'schema': 'golden-v2-reference-companions-v1', 'job_id': job_id,
                'source_sha256': source_sha, 'references': rows,
                'auto_replacement_allowed': False, 'human_listening_attested': False}
    store_immutable_bytes(root / 'trusted-reference-manifest.json',
                          json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode())
    return {'status': 'MATCHED', 'references': len(rows)}


def reference_comparison_hint(*, job_dir: Path, job_id: str,
                              source_sha: str, chirp_words: list[dict]) -> dict | None:
    """Review-only text differences. No change to primary/Chirp QA gates."""
    root = Path(job_dir) / 'golden-v2'
    path = root / 'trusted-reference-manifest.json'
    if not path.exists():
        return None
    if path.is_symlink():
        raise V2Conflict('reference manifest symlink forbidden')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if (manifest.get('schema') != 'golden-v2-reference-companions-v1'
            or manifest.get('job_id') != job_id
            or manifest.get('source_sha256') != source_sha):
        raise V2Conflict('reference manifest source mismatch')
    hints = []
    for ref in manifest['references']:
        if not isinstance(ref.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', ref['sha256']):
            raise V2Conflict('invalid pinned reference digest')
        filename = 'trusted-reference-' + ref['sha256'] + '.' + ref['format']
        if ref['local_filename'] != filename or ref['format'] not in {'txt', 'srt'}:
            raise V2Conflict('invalid reference artifact path')
        artifact = root / filename
        if artifact.is_symlink():
            raise V2Conflict('reference artifact symlink forbidden')
        raw = artifact.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise V2Conflict('reference artifact modified')
        text = raw.decode('utf-8-sig')
        if ref['format'] == 'srt':
            text = '\n'.join(line for line in text.splitlines() if not re.match(
                r'^\s*(?:\d+|\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->)\s*$', line)
                and '-->' not in line)
        try:
            if len(text) > 200_000:
                raise AlignmentNeedsReview('reference too long for bounded automatic diff')
            result = compare_gemini_chirp(text, chirp_words, source_sha256=source_sha)
            hints.append({'reference_sha256': ref['sha256'], 'format': ref['format'],
                          'status': 'REVIEW_ONLY', 'disagreements': result['pending_review_count'],
                          'candidates': result['candidates'][:100],
                          'truncated': result['pending_review_count'] > 100})
        except AlignmentNeedsReview:
            hints.append({'reference_sha256': ref['sha256'], 'format': ref['format'],
                          'status': 'ALIGNMENT_REVIEW_REQUIRED', 'disagreements': None,
                          'candidates': []})
    return {'schema': 'golden-v2-reference-comparison-v1',
            'reference_is_acoustic_evidence': False,
            'auto_replacement_allowed': False, 'human_listening_attested': False,
            'references': hints}
