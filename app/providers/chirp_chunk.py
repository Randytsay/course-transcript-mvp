"""Run and verify exactly one Chirp 3 chunk using GCS output."""
from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech

ROOT=Path('/app'); JOB_NAME=os.environ.get('JOB_NAME','voice_11386603-seg1'); JOB=ROOT/'data'/'jobs'/JOB_NAME
def atomic(path:Path, data:object):
    temp=path.with_suffix(path.suffix+'.tmp'); temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); temp.replace(path)
def ms(v): return round(v.total_seconds()*1000)
def has_speech(path:Path)->bool:
    result=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),'-af','volumedetect','-f','null','-'],capture_output=True,text=True,check=False,timeout=180)
    match=re.search(r'mean_volume:\s*(-?\d+(?:\.\d+)?) dB',result.stderr)
    if not match: raise RuntimeError('unable to determine chunk audio volume')
    return float(match.group(1)) > float(os.getenv('CHIRP_SPEECH_MEAN_VOLUME_DB','-50'))
def main():
    index=int(os.environ['CHUNK_INDEX']); start=float(os.environ['CHUNK_START_SECONDS']); end=float(os.environ['CHUNK_END_SECONDS']); name=f'chunk-{index:03d}'; CHUNK=JOB/'chunks'/name
    project,bucket_name=os.environ['GOOGLE_CLOUD_PROJECT'],os.environ['GCS_BUCKET']; CHUNK.mkdir(parents=True,exist_ok=True)
    audio=CHUNK/'audio.flac'; subprocess.run(['ffmpeg','-y','-ss',str(start),'-i',str(JOB/'normalized.flac'),'-t',str(end-start),'-c:a','flac',str(audio)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    bucket=storage.Client().bucket(bucket_name); object_name=f'jobs/{JOB_NAME}/chunks/{name}/audio.flac'; blob=bucket.blob(object_name); blob.upload_from_filename(audio,content_type='audio/flac')
    client=speech_v2.SpeechClient(client_options={'api_endpoint':'us-speech.googleapis.com'})
    config=cloud_speech.RecognitionConfig(auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),language_codes=[os.getenv('LANGUAGE_CODE','cmn-Hant-TW')],model='chirp_3',features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True))
    uri=f'gs://{bucket_name}/{object_name}'; out=f'gs://{bucket_name}/jobs/{JOB_NAME}/chunks/{name}/chirp-output/'
    op=client.batch_recognize(request=cloud_speech.BatchRecognizeRequest(recognizer=f'projects/{project}/locations/us/recognizers/_',config=config,files=[cloud_speech.BatchRecognizeFileMetadata(uri=uri)],recognition_output_config=cloud_speech.RecognitionOutputConfig(gcs_output_config=cloud_speech.GcsOutputConfig(uri=out))))
    role=os.getenv('CHUNK_ROLE','base')
    atomic(CHUNK/'manifest.json',{'chunk_index':index,'role':role,'source_start_ms':round(start*1000),'source_end_ms':round(end*1000),'operation_name':op.operation.name,'status':'SUBMITTED','created_at':datetime.now(UTC).isoformat()})
    if os.getenv('SUBMIT_ONLY') == '1':
        print(f'CHIRP_{name}=SUBMITTED operation={op.operation.name}')
        return
    response=op.result(timeout=3600); fr=response.results[uri]; result_kind=fr._pb.WhichOneof('result'); error={'code':fr.error.code,'message':fr.error.message} if fr.error and fr.error.code else None
    record={'chunk_index':index,'role':role,'source_start_ms':round(start*1000),'source_end_ms':round(end*1000),'operation_name':op.operation.name,'status':'FAILED','error':error,'result_oneof':result_kind,'google_cloud_speech_version':importlib.metadata.version('google-cloud-speech'),'output_field':None,'gcs_uri':None,'created_at':datetime.now(UTC).isoformat()}
    if (error and error['code']!=0) or result_kind!='cloud_storage_result': atomic(CHUNK/'manifest.json',record); raise RuntimeError(str(record))
    csr=fr.cloud_storage_result; field='native_format_uri' if getattr(csr,'native_format_uri','') else 'uri'; result_uri=getattr(csr,field,'')
    if not result_uri:
        candidates=list(bucket.list_blobs(prefix=f'jobs/{JOB_NAME}/chunks/{name}/chirp-output/'))
        if len(candidates) != 1: raise RuntimeError(f'Expected one GCS output object, found {len(candidates)}')
        result_uri=f'gs://{bucket_name}/{candidates[0].name}'; field='gcs_prefix_fallback'
    record.update(output_field=field,gcs_uri=result_uri)
    rb,rn=result_uri.removeprefix('gs://').split('/',1); raw=storage.Client().bucket(rb).blob(rn).download_as_text(); raw_temp=CHUNK/'chirp-raw.json.tmp'; raw_temp.write_text(raw,encoding='utf-8'); raw_temp.replace(CHUNK/'chirp-raw.json')
    parsed=cloud_speech.BatchRecognizeResults.from_json(raw); words=[]
    for r in parsed.results:
      if r.alternatives:
       for w in r.alternatives[0].words: words.append({'word':w.word,'start_ms':ms(w.start_offset)+round(start*1000),'end_ms':ms(w.end_offset)+round(start*1000)})
    status='SUCCEEDED'
    if not words:
      if has_speech(audio):
       record.update(status='FAILED',error={'code':'EMPTY_WITH_SPEECH','message':'Chirp returned no words for audible speech'},word_count=0,max_end_ms=0); atomic(CHUNK/'manifest.json',record); raise RuntimeError('Chirp returned no words for audible chunk')
      status='EMPTY_SILENCE'
    record.update(status=status,word_count=len(words),max_end_ms=max((w['end_ms'] for w in words),default=0)); atomic(CHUNK/'words.json',{'chunk_index':index,'words':words}); atomic(CHUNK/'manifest.json',record)
    audio.unlink(missing_ok=True)
    print(f'CHIRP_{name}=PASS status={status} words={len(words)}')
if __name__=='__main__': main()
