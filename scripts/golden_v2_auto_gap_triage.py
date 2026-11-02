"""Local-only acoustic review: no ASR, Drive, GCP or SQLite mutations."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from app.providers.golden_v2_auto_gap_triage import inspect_subtitle_gaps


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir',type=Path,required=True)
    parser.add_argument('--job-id')
    parser.add_argument('--source-sha',required=True)
    parser.add_argument('--srt',type=Path,required=True)
    args=parser.parse_args()
    source=args.job_dir/'normalized.flac'
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                         '-of','default=noprint_wrappers=1:nokey=1',str(source)],
                         check=True,text=True,capture_output=True,timeout=30)
    result=inspect_subtitle_gaps(job_dir=args.job_dir,source_sha256=args.source_sha,
        duration_ms=round(float(probe.stdout.strip())*1000),srt_bytes=args.srt.read_bytes())
    if args.job_id:
        result['job_id']=args.job_id
    folder=args.job_dir/'golden-v2'
    output=folder/f"auto-gap-triage-{result['srt_sha256'][:20]}.json"
    data=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if output.exists():
        if output.is_symlink() or output.read_bytes()!=data:
            parser.error('existing triage differs; cannot overwrite')
    else:
        descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(descriptor,'wb') as out:
            out.write(data);out.flush();os.fsync(out.fileno())
    print(json.dumps({'path':str(output),'total_gaps':result['total_gaps'],
             'confirmed_nonspeech':result['nonspeech_confirmed_count'],
             'speech_candidates':result['speech_confirmed_count'],
             'review_count':result['review_count'],'paid_calls':False}))

if __name__=='__main__':main()
