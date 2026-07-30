"""Resumable 10-second Gemini 3.6 Flash transcription for completeness."""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from google import genai
from google.cloud import storage
from google.genai import types

ROOT = Path('/app')
SOURCE = ROOT / 'data' / 'input' / '語音 260724_162531.m4a'
TMP, RESULTS = ROOT / 'tmp', ROOT / 'data' / 'results'
WORK = RESULTS / 'phase8-work'
SAMPLE_SECONDS, CHUNK_SECONDS = 300, 10

SCHEMA = {'type':'object','properties':{'language':{'type':'string'},'segments':{'type':'array','items':{'type':'object','properties':{'start_ms':{'type':'integer'},'end_ms':{'type':'integer'},'speaker':{'type':'string'},'text_verbatim':{'type':'string'},'unclear':{'type':'boolean'}},'required':['start_ms','end_ms','speaker','text_verbatim','unclear']}}},'required':['language','segments']}

def one(spec: tuple[int, int]) -> dict[str, object]:
    start_s, duration_s = spec
    result_path = WORK / f'chunk-{start_s:04d}.json'
    if result_path.exists():
        return json.loads(result_path.read_text(encoding='utf-8'))
    project, bucket_name, model = os.environ['GOOGLE_CLOUD_PROJECT'], os.environ['GCS_BUCKET'], os.environ['PHASE2_MODEL']
    if model != 'gemini-3.6-flash': raise RuntimeError('Phase 8 is locked to gemini-3.6-flash.')
    audio = TMP / f'phase8-{start_s:04d}.flac'; object_name=f'test/phase8/chunk-{start_s:04d}.flac'
    subprocess.run(['ffmpeg','-y','-ss',str(start_s),'-i',str(SOURCE),'-t',str(duration_s),'-ac','1','-ar','16000','-c:a','flac',str(audio)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    bucket = storage.Client().bucket(bucket_name); blob=bucket.blob(object_name)
    try:
        blob.upload_from_filename(audio, content_type='audio/flac')
        client=genai.Client(vertexai=True,project=project,location=os.getenv('GOOGLE_CLOUD_LOCATION','global'))
        response=client.models.generate_content(model=model,contents=['Transcribe this Traditional-Chinese recording faithfully. Return JSON only. Use local audio milliseconds, Speaker A/B labels only, preserve spoken wording, and use [聽不清] with unclear=true instead of guessing. Do not summarize or omit speech.',types.Part.from_uri(file_uri=f'gs://{bucket_name}/{object_name}',mime_type='audio/flac')],config=types.GenerateContentConfig(response_mime_type='application/json',response_json_schema=SCHEMA,audio_timestamp=True,temperature=0,max_output_tokens=8192))
        transcript=json.loads(response.text); usage=response.usage_metadata
        payload={'source_start_ms':start_s*1000,'source_end_ms':(start_s+duration_s)*1000,'segments':transcript['segments'],'segment_count':len(transcript['segments']),'usage_metadata':usage.model_dump(mode='json') if usage else None}
        temporary=result_path.with_suffix('.tmp'); temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); temporary.replace(result_path)
        print(f'chunk={start_s:03d} PASS', flush=True)
        return payload
    finally:
        audio.unlink(missing_ok=True)
        if blob.exists(): blob.delete()

def main() -> int:
    TMP.mkdir(parents=True,exist_ok=True); WORK.mkdir(parents=True,exist_ok=True)
    specs=[(start,min(CHUNK_SECONDS,SAMPLE_SECONDS-start)) for start in range(0,SAMPLE_SECONDS,CHUNK_SECONDS)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        chunks=list(pool.map(one,specs))
    combined='\n'.join(s['text_verbatim'] for c in chunks for s in c['segments'])
    payload={'created_at':datetime.now(UTC).isoformat(),'model':os.environ['PHASE2_MODEL'],'location':os.getenv('GOOGLE_CLOUD_LOCATION','global'),'sample_seconds':SAMPLE_SECONDS,'chunk_seconds':CHUNK_SECONDS,'chunks':chunks}
    (RESULTS/'phase8-gemini-3.6-flash-10s.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (RESULTS/'phase8-gemini-3.6-flash-10s.txt').write_text(combined+'\n',encoding='utf-8')
    print(f'PHASE8_GEMINI=PASS chunks={len(chunks)} chars={len(combined)}',flush=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
