"""Recover completed chunk-000 from its existing GCS result without a new ASR call."""
from __future__ import annotations
import json, os
from pathlib import Path
from google.cloud import storage
from google.cloud.speech_v2.types import cloud_speech
ROOT=Path('/app'); CHUNK=ROOT/'data'/'jobs'/'voice_11386603-seg1'/'chunks'/'chunk-000'
def ms(v): return round(v.total_seconds()*1000)
def main():
 b=storage.Client().bucket(os.environ['GCS_BUCKET']); blobs=list(b.list_blobs(prefix='jobs/voice_11386603-seg1/chunks/chunk-000/chirp-output/'))
 if len(blobs)!=1: raise RuntimeError(f'Expected one result object, found {len(blobs)}')
 raw=blobs[0].download_as_text(); (CHUNK/'chirp-raw.json').write_text(raw,encoding='utf-8'); parsed=cloud_speech.BatchRecognizeResults.from_json(raw); words=[]
 for r in parsed.results:
  for w in r.alternatives[0].words: words.append({'word':w.word,'start_ms':ms(w.start_offset),'end_ms':ms(w.end_offset)})
 payload={'chunk_index':0,'source_start_ms':0,'source_end_ms':900000,'status':'SUCCEEDED' if words else 'EMPTY','gcs_uri':f'gs://{b.name}/{blobs[0].name}','word_count':len(words),'max_end_ms':max((w['end_ms'] for w in words),default=0)}
 (CHUNK/'words.json').write_text(json.dumps({'chunk_index':0,'words':words},ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); (CHUNK/'manifest.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(f'RECOVER=PASS words={len(words)}')
if __name__=='__main__': main()
