"""Admissibility gate for text corrections over timing-authoritative Chirp text.

Chirp 3 owns word timing and its text is the default deliverable. A second
engine's *unlistened* reading may propose a change but must not silently
overwrite it. On 2026-10-23 exactly that happened in the delivered 251018
review copy: one Gemini-primary reading rewrote 萬宗歸一 as 中取一.

Exactly two evidence classes may overwrite Chirp text:

``HUMAN_LISTENING``   a named human reviewer attests hearing the span.
``AUDIO_NATIVE_PROBE`` a source-bound probe of a physical clip that covers the
                       whole edited span and whose response text contains the
                       replacement.

Everything else is downgraded: the Chirp text is retained and the proposal is
kept as an unresolved candidate for a listening session. This gate never
decides the correct wording, never reads a file and never calls a provider.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

PROBE_RECEIPT_SCHEMA = 'golden-v2-audio-native-probe-receipt-v1'

_HEX = re.compile(r'[0-9a-f]{64}')
# Identities that describe a model, never a person who listened.
_NON_HUMAN_REVIEWERS = {'AI', 'AI_SOURCE_BOUND_TEXT_REVIEW', 'MODEL', 'SYSTEM'}
_PUNCTUATION = re.compile(r'[\s,，。．、？?！!：:；;「」『』（）()\[\]【】\-—…·・]')


class TextCorrectionGateError(RuntimeError):
    """Raised when a correction batch is malformed or blocks delivery."""


def _normalize(text: str) -> str:
    return _PUNCTUATION.sub('', text)


def _span_text(words: list[dict[str, Any]], indices: list[int]) -> tuple[str, int, int]:
    rendered = ''.join(str(words[index]['word']) for index in indices)
    start = min(int(words[index]['start_ms']) for index in indices)
    end = max(int(words[index]['end_ms']) for index in indices)
    return rendered, start, end


def _validate_shape(corrections: Iterable[Mapping[str, Any]],
                    words: list[dict[str, Any]]) -> list[Mapping[str, Any]]:
    records = list(corrections or [])
    for record in records:
        if not isinstance(record, Mapping):
            raise TextCorrectionGateError('each correction must be a mapping')
        indices = record.get('source_word_indices')
        if (not isinstance(indices, list) or not indices
                or any(type(index) is not int for index in indices)
                or indices != sorted(indices) or len(set(indices)) != len(indices)
                or min(indices) < 0 or max(indices) >= len(words)):
            raise TextCorrectionGateError(
                'correction must address a real, ordered range of merged words')
        if not isinstance(record.get('before'), str) or not record['before'].strip():
            raise TextCorrectionGateError('correction must state the Chirp text it edits')
        after = record.get('after')
        if not isinstance(after, str) or not after.strip():
            raise TextCorrectionGateError('correction must state a non-empty replacement')
        if after == record['before']:
            raise TextCorrectionGateError('a correction must change the Chirp text')
    return records


def _probe_support(record: Mapping[str, Any], span: tuple[int, int],
                   receipts: Mapping[str, Any], source_sha: str) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    digest = record.get('probe_receipt_sha256')
    if not digest:
        return None, reasons
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        return None, ['probe_receipt_reference_malformed']
    receipt = receipts.get(digest)
    if not isinstance(receipt, Mapping):
        return None, ['probe_receipt_missing']
    start, end = span
    if receipt.get('schema') != PROBE_RECEIPT_SCHEMA:
        return None, ['probe_receipt_malformed']
    if receipt.get('source_sha256') != source_sha:
        return None, ['probe_source_mismatch']
    if (type(receipt.get('clip_start_ms')) is not int
            or type(receipt.get('clip_end_ms')) is not int
            or receipt['clip_end_ms'] <= receipt['clip_start_ms']
            or not isinstance(receipt.get('response_text'), str)
            or not isinstance(receipt.get('model'), str)
            or not isinstance(receipt.get('clip_sha256'), str)
            or not _HEX.fullmatch(receipt['clip_sha256'])):
        return None, ['probe_receipt_malformed']
    if not (receipt['clip_start_ms'] <= start and receipt['clip_end_ms'] >= end):
        return None, ['probe_clip_does_not_cover_edited_span']
    if _normalize(str(record['after'])) not in _normalize(receipt['response_text']):
        return None, ['probe_text_does_not_support_replacement']
    return dict(receipt, sha256=digest), reasons


def _listening_support(record: Mapping[str, Any]) -> tuple[bool, list[str]]:
    if record.get('human_listening_attested') is not True:
        return False, []
    reviewer = record.get('reviewer')
    reference = record.get('review_reference')
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reference, str) or not reference.strip():
        return False, ['listening_attestation_incomplete']
    if reviewer.strip().upper() in _NON_HUMAN_REVIEWERS or reviewer.strip().lower().startswith(('ai_', 'ai-', 'model')):
        return False, ['listener_is_not_human']
    return True, []


def assess_text_corrections(
    *, corrections: Iterable[Mapping[str, Any]], words: list[dict[str, Any]],
    source_sha: str, probe_receipts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify every proposed correction; never decide the correct wording."""
    if not isinstance(words, list) or not words:
        raise TextCorrectionGateError('merged word timeline is required')
    if not isinstance(source_sha, str) or not _HEX.fullmatch(source_sha):
        raise TextCorrectionGateError('a full source SHA is required')
    receipts = dict(probe_receipts or {})
    items: list[dict[str, Any]] = []
    for record in _validate_shape(corrections, words):
        indices = list(record['source_word_indices'])
        rendered, start, end = _span_text(words, indices)
        reasons: list[str] = []
        evidence_class = 'SECOND_ENGINE_TEXT_ONLY'
        evidence_sha: str | None = None
        if rendered != record['before']:
            reasons.append('before_text_does_not_match_chirp_words')
        probe, probe_reasons = _probe_support(record, (start, end), receipts, source_sha)
        listened, listening_reasons = _listening_support(record)
        if listened:
            evidence_class = 'HUMAN_LISTENING'
        elif probe is not None:
            evidence_class = 'AUDIO_NATIVE_PROBE'
            evidence_sha = probe['sha256']
        else:
            reasons.extend(probe_reasons or listening_reasons)
            reasons.append('unlistened_second_engine_reading')
        status = 'ADMISSIBLE' if evidence_class != 'SECOND_ENGINE_TEXT_ONLY' and not reasons else 'DOWNGRADED_TO_CHIRP'
        items.append({
            'correction_id': record.get('id'),
            'status': status,
            'evidence_class': evidence_class,
            'evidence_sha256': evidence_sha,
            'reasons': sorted(set(reasons)),
            'start_ms': start,
            'end_ms': end,
            'word_indices': indices,
            'retained_text': rendered,
            'claimed_before': record['before'],
            'proposed_text': record['after'],
            'human_listening_attested': False,
            'second_engine_context_present': bool(
                (record.get('native_primary_context') or '').strip()),
        })
    blocking = [item for item in items if item['status'] != 'ADMISSIBLE']
    return {
        'schema': 'golden-v2-text-correction-gate-v1',
        'correction_count': len(items),
        'admissible_count': len(items) - len(blocking),
        'downgraded_count': len(blocking),
        'blocking_correction_count': len(blocking),
        'retained_chirp_only': not items,
        'safe_to_publish': not blocking,
        'timing_authority': 'chirp_3',
        'golden_pass': False,
        'human_listening_attested': False,
        'second_engine_text_is_not_acoustic_evidence': True,
        'rule': ('Only a named human listener or a source-bound audio-native probe '
                 'covering the whole edited span may overwrite Chirp text.'),
        'corrections': items,
    }


def require_admissible_text_corrections(
    *, corrections: Iterable[Mapping[str, Any]], words: list[dict[str, Any]],
    source_sha: str, probe_receipts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the report, raising with every blocking correction named."""
    report = assess_text_corrections(corrections=corrections, words=words,
                                     source_sha=source_sha,
                                     probe_receipts=probe_receipts)
    if not report['safe_to_publish']:
        detail = '; '.join(
            f"id={item['correction_id']} {item['retained_text']}->"
            f"{item['proposed_text']} via {item['evidence_class']} "
            f"({','.join(item['reasons'])})"
            for item in report['corrections'] if item['status'] != 'ADMISSIBLE')
        raise TextCorrectionGateError(
            'text corrections would overwrite Chirp text without admissible '
            f"evidence: {detail}")
    return report