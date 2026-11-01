"""Bounded provider-free acoustic classification of subtitle-uncovered spans.

This is a reproducible advisory, not a proof that every spoken word was found.
Never mutates SRT/TXT, paid operations, Drive, human attestations or Golden QA.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_acoustic_qa import _probe_nonspeech
from app.providers.golden_v2_auto_acceptance import _check_cues

SCHEMA='golden-v2-auto-gap-triage-v1'


def _sha_file(file: Path) -> str:
    checksum=hashlib.sha256()
    with file.open('rb') as inp:
        for block in iter(lambda:inp.read(1024*1024),b''):
            checksum.update(block)
    return checksum.hexdigest()


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
        if dispositions and all(v=='NONSPEECH_CONFIRMED' for v in dispositions):
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
                      duration_ms: int) -> dict | None:
    """Only accept exact source/SRT/timebase matches; unknown = no classification."""
    digest=hashlib.sha256(srt_bytes).hexdigest()
    path=Path(job_dir)/'golden-v2'/f'auto-gap-triage-{digest[:20]}.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size>4_000_000:
        raise V2Conflict('unsafe triage sidecar')
    report=json.loads(path.read_bytes())
    if (report.get('schema')!=SCHEMA
            or report.get('source_sha256')!=source_sha256
            or report.get('srt_sha256')!=digest
            or report.get('duration_ms')!=duration_ms
            or report.get('total_gaps')!=len(report.get('gaps',[]))):
        raise V2Conflict('stale acoustic triage cannot be used for this source')
    for gap in report['gaps']:
        if gap.get('disposition')=='NONSPEECH_CONFIRMED':
            probes=gap.get('probes') or []
            if (not probes or any(v.get('disposition')!='NONSPEECH_CONFIRMED'
                    for v in probes)
                    or any(type(v.get('start_ms')) is not int or
                           type(v.get('end_ms')) is not int for v in probes)
                    or probes[0]['start_ms']!=gap['start_ms']
                    or probes[-1]['end_ms']!=gap['end_ms']
                    or any(a['end_ms']!=b['start_ms'] for a,b in zip(probes,probes[1:]))
                    or any(not (isinstance(v.get('max_volume_dbfs'),(int,float))
                                    and v['max_volume_dbfs']<=-65
                                    or (v.get('vad') or {}).get('robust_non_speech') is True)
                           for v in probes)):
                raise V2Conflict('non-speech claimed without contiguous audio probe evidence')
    return report
