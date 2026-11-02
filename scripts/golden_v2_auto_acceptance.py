"""Offline auto acceptance; report is NOT Golden PASS or paid-stage closure.

A trusted operator must provide the original job's normalized FLAC and its
canonical source SHA. Does not open Production SQLite or Drive, or invoke models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from app.providers.golden_v2_auto_acceptance import (
    evaluate_auto_acceptance, render_human_review_advisory,
)
from app.providers.golden_v2_auto_gap_triage import (
    _finite_float, _reject_constant, _unique_json_object,
)


def _sha_stream(path: Path) -> str:
    digest=hashlib.sha256()
    if path.is_symlink():
        raise ValueError('symlinked audio not accepted')
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('job-id','source-sha','audio','srt','txt','title','output-json','output-report'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--reference-txt')
    parser.add_argument('--original-srt')
    parser.add_argument('--acoustic-review-json')
    parser.add_argument('--chirp-stage',default='PENDING')
    args=parser.parse_args()
    audio=Path(args.audio)
    if any(Path(p).exists() for p in (args.output_json,args.output_report)):
        parser.error('outputs must not exist (create only)')
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                          '-of','default=noprint_wrappers=1:nokey=1',str(audio)],
                         check=True,text=True,capture_output=True,timeout=30)
    duration=round(float(probe.stdout.strip())*1000)
    observed=_sha_stream(audio)
    if observed != args.source_sha:
        parser.error('canonical source SHA mismatch')
    review=None
    if args.acoustic_review_json:
        raw=json.loads(Path(args.acoustic_review_json).read_text(),
                       object_pairs_hook=_unique_json_object,
                       parse_float=_finite_float,parse_constant=_reject_constant)
        if (raw.get('job_id') and raw.get('job_id') != args.job_id) or raw.get('source_sha256') != args.source_sha:
            parser.error('acoustic review provenance mismatch')
        review=raw.get('review_items') if 'review_items' in raw else raw.get('gaps')
    def optional(path):
        return Path(path).read_bytes() if path else None
    result=evaluate_auto_acceptance(
        job_id=args.job_id,source_sha256=args.source_sha,
        source_audio_sha_verified=True,source_duration_ms=duration,
        srt_bytes=Path(args.srt).read_bytes(),txt_bytes=Path(args.txt).read_bytes(),
        reference_txt_bytes=optional(args.reference_txt),
        original_srt_bytes=optional(args.original_srt),
        acoustic_review_items=review,chirp_stage=args.chirp_stage)
    raw_json=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()
    report=render_human_review_advisory(result,lesson_title=args.title).encode()
    for path,payload in ((args.output_json,raw_json),(args.output_report,report)):
        # Writer can only create brand-new files and never mutates source assets.
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(payload);out.flush();os.fsync(out.fileno())
    print(json.dumps({'job_id':args.job_id,'auto_status':result['status'],
                      'golden_pass':False,'srt_sha256':result['artifact_shas']['srt_sha256']}))


if __name__=='__main__':
    main()
