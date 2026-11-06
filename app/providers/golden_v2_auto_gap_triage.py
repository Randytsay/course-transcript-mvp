"""Bounded provider-free acoustic classification of subtitle-uncovered spans.

This is a reproducible advisory, not a proof that every spoken word was found.
Never mutates SRT/TXT, paid operations, Drive, human attestations or Golden QA.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_acoustic_qa import _probe_nonspeech
from app.providers.golden_v2_auto_acceptance import _check_cues, _nonspeech_probes_valid

SCHEMA='golden-v2-auto-gap-triage-v1'


def _unique_json_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def _sha_file(file: Path) -> str:
    checksum=hashlib.sha256()
    with file.open('rb') as inp:
        for block in iter(lambda:inp.read(1024*1024),b''):
            checksum.update(block)
    return checksum.hexdigest()


def _finite_float(text):
    value=float(text)
    if not math.isfinite(value):
        raise ValueError('non-finite JSON number')
    return value


def _reject_constant(text):
    raise ValueError('non-finite JSON constant')


def inspect_subtitle_gaps(*, job_dir: Path, source_sha256: str,
                          duration_ms: int, srt_bytes: bytes,
                          min_gap_ms: int=3000, max_gaps: int=32,
                          max_slices: int=128, slice_ms: int=30000) -> dict:
    if (not isinstance(source_sha256,str) or len(source_sha256)!=64
            or not isinstance(duration_ms,int) or duration_ms<=0
            or not 1000<=min_gap_ms<=60000 or not 1<=max_gaps<=512
            or not 1<=max_slices<=4096 or not 1000<=slice_ms<=30000):
        raise V2Conflict('invalid acoustic triage budget or source')
    source=Path(job_dir)/'normalized.flac'
    if source.is_symlink() or not source.is_file() or _sha_file(source)!=source_sha256:
        raise V2Conflict('auto-gap triage must use canonical original FLAC bytes')
    cues,errs=_check_cues(srt_bytes)
    if errs or not cues or cues[-1]['end_ms']>duration_ms+250:
        raise V2Conflict('valid bounded subtitle timing required')
    gaps=[];cursor=0
    for cue in cues:
        if cue['start_ms']-cursor>=min_gap_ms:
            gaps.append((cursor,cue['start_ms']))
        cursor=max(cursor,cue['end_ms'])
    if duration_ms-cursor>=min_gap_ms:
        gaps.append((cursor,duration_ms))
    output=[];slices=0
    for index,(start,end) in enumerate(gaps):
        if index>=max_gaps:
            output.append({'index':index,'start_ms':start,'end_ms':end,
                           'disposition':'REVIEW','reason':'triage_gap_budget_exceeded'})
            continue
        needed=(end-start+slice_ms-1)//slice_ms
        if slices+needed>max_slices:
            output.append({'index':index,'start_ms':start,'end_ms':end,
                           'disposition':'REVIEW','reason':'triage_slice_budget_exceeded'})
            continue
        checks=[];cursor=start
        while cursor<end:
            next_stop=min(cursor+slice_ms,end)
            checks.append(_probe_nonspeech(Path(job_dir),cursor,next_stop))
            cursor=next_stop;slices+=1
        dispositions=[v['disposition'] for v in checks]
        if (dispositions and all(v=='NONSPEECH_CONFIRMED' for v in dispositions)
                and _nonspeech_probes_valid({'probes':checks},start,end)):
            disposition='NONSPEECH_CONFIRMED'
        elif 'SPEECH_CONFIRMED' in dispositions:
            disposition='SPEECH_CONFIRMED'
        else:
            disposition='REVIEW'
        output.append({'index':index,'start_ms':start,'end_ms':end,
                       'disposition':disposition,'reason':'local_segmented_audio_evidence',
                       'slice_count':len(checks),
                       'probes':[{key:v.get(key) for key in
                           ('start_ms','end_ms','disposition','max_volume_dbfs','vad')}
                           for v in checks]})
    return {'schema':SCHEMA,'source_sha256':source_sha256,
            'srt_sha256':hashlib.sha256(srt_bytes).hexdigest(),
            'duration_ms':duration_ms,'gap_min_ms':min_gap_ms,
            'gaps':output,'total_gaps':len(gaps),
            'nonspeech_confirmed_count':sum(x['disposition']=='NONSPEECH_CONFIRMED' for x in output),
            'speech_confirmed_count':sum(x['disposition']=='SPEECH_CONFIRMED' for x in output),
            'review_count':sum(x['disposition']=='REVIEW' for x in output),
            'no_paid_provider_calls':True,'human_listening_attested':False,
            'golden_qa_pass':False}


def load_bound_triage(*, job_dir: Path, source_sha256: str, srt_bytes: bytes,
                      duration_ms: int, include_source_sha256: bool = False):
    """Only accept exact source/SRT/timebase matches; unknown = no classification."""
    digest=hashlib.sha256(srt_bytes).hexdigest()
    path=Path(job_dir)/'golden-v2'/f'auto-gap-triage-{digest[:20]}.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size>4_000_000:
        raise V2Conflict('unsafe triage sidecar')
    try:
        raw=path.read_bytes()
        report=json.loads(raw, object_pairs_hook=_unique_json_object,
                          parse_float=_finite_float, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, OSError, RecursionError) as exc:
        raise V2Conflict('invalid acoustic triage sidecar') from exc
    if (not isinstance(report,dict) or not isinstance(report.get('gaps'),list)
            or type(report.get('total_gaps')) is not int):
        raise V2Conflict('invalid acoustic triage inventory')
    if (report.get('schema')!=SCHEMA
            or report.get('source_sha256')!=source_sha256
            or report.get('srt_sha256')!=digest
            or report.get('duration_ms')!=duration_ms
            or report.get('total_gaps')!=len(report.get('gaps',[]))):
        raise V2Conflict('stale acoustic triage cannot be used for this source')
    # Reconstruct the inventory from the actual SRT, not the sidecar's counts.
    minimum=report.get('gap_min_ms')
    cues, errors=_check_cues(srt_bytes)
    if (type(minimum) is not int or not 1000<=minimum<=60000 or errors or not cues
            or cues[-1]['end_ms']>duration_ms+250 or not isinstance(report['gaps'],list)):
        raise V2Conflict('invalid acoustic triage inventory')
    expected=set(); cursor=0; excluded_short=0
    for cue in cues:
        if cue['start_ms']-cursor>=minimum:
            expected.add((cursor,cue['start_ms']))
        elif cue['start_ms']>cursor:
            excluded_short+=1
        cursor=max(cursor,cue['end_ms'])
    if duration_ms-cursor>=minimum:
        expected.add((cursor,duration_ms))
    elif duration_ms>cursor:
        excluded_short+=1
    seen=set()
    expected_indexes={bounds:index for index,bounds in enumerate(sorted(expected))}
    for gap in report['gaps']:
        if not isinstance(gap,dict):
            raise V2Conflict('invalid acoustic triage gap')
        start,end=gap.get('start_ms'),gap.get('end_ms')
        if (type(start) is not int or type(end) is not int or (start,end) not in expected
                or ('index' in gap and (type(gap['index']) is not int
                                        or gap['index'] != expected_indexes[(start,end)]))
                or any(name in gap and (type(gap[name]) is not int or gap[name] != value)
                       for name,value in (('gap_start_ms',start),('gap_end_ms',end)))
                or (start,end) in seen or ('acoustic_disposition' in gap and
                    gap['acoustic_disposition'] != gap.get('disposition'))
                or gap.get('disposition') not in
                ('NONSPEECH_CONFIRMED','SPEECH_CONFIRMED','REVIEW','REVIEW_REQUIRED')):
            raise V2Conflict('acoustic triage gap does not match subtitle inventory')
        seen.add((start,end))
        if gap.get('disposition')=='NONSPEECH_CONFIRMED':
            if not _nonspeech_probes_valid(gap,start,end):
                raise V2Conflict('non-speech claimed without contiguous audio probe evidence')
    # Preserve omitted inventory as explicit unresolved items, including gaps
    # shorter than the acceptance report's separate fixed 3-second threshold.
    missing=expected-seen
    report['sidecar_reported_gap_count']=len(report['gaps'])
    report['inventory_expected_count']=len(expected)
    report['inventory_missing_count']=len(missing)
    report['inventory_excluded_short_gap_count']=excluded_short
    report['gaps'] = report['gaps'] + [
        {'start_ms':start,'end_ms':end,'disposition':'REVIEW',
         'reason':'sidecar_inventory_missing'} for start,end in sorted(missing)]
    report['total_gaps']=len(report['gaps'])
    for disposition,key in (('NONSPEECH_CONFIRMED','nonspeech_confirmed_count'),
                            ('SPEECH_CONFIRMED','speech_confirmed_count')):
        report[key]=sum(gap['disposition']==disposition for gap in report['gaps'])
    report['review_count']=sum(gap['disposition'] in ('REVIEW','REVIEW_REQUIRED') for gap in report['gaps'])
    # Sidecar assertions cannot attest a human listening or a Golden gate.
    report['human_listening_attested']=False
    report['golden_qa_pass']=False
    return (report, hashlib.sha256(raw).hexdigest()) if include_source_sha256 else report
