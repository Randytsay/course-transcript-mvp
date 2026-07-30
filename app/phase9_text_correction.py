"""Use Gemini 3.6 Flash to correct complete Chirp text windows without audio."""
from __future__ import annotations
import concurrent.futures, json, os
from datetime import UTC, datetime
from pathlib import Path
from google import genai
from google.genai import types

ROOT=Path('/app'); RESULTS=ROOT/'data'/'results'; WORK=RESULTS/'phase9-work'
SCHEMA={'type':'object','properties':{'corrected_text':{'type':'string'},'uncertain_terms':{'type':'array','items':{'type':'string'}}},'required':['corrected_text','uncertain_terms']}

def one(item):
    index,start,end,chirp_text=item; path=WORK/f'window-{index:02d}.json'
    if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
    model=os.environ['PHASE2_MODEL']
    if model!='gemini-3.6-flash': raise RuntimeError('Phase 9 is locked to gemini-3.6-flash.')
    client=genai.Client(vertexai=True,project=os.environ['GOOGLE_CLOUD_PROJECT'],location=os.getenv('GOOGLE_CLOUD_LOCATION','global'))
    prompt=f'''You are correcting an ASR transcript for a Traditional-Chinese course. Return JSON only. Preserve every spoken idea and do not summarize. Convert Simplified Chinese to Traditional Chinese, correct only clear ASR errors, preserve English names/acronyms, and list uncertain terms instead of guessing. This is the complete source text for {start/1000:.0f}-{end/1000:.0f} seconds:\n{chirp_text}'''
    response=client.models.generate_content(model=model,contents=prompt,config=types.GenerateContentConfig(response_mime_type='application/json',response_json_schema=SCHEMA,temperature=0))
    answer=json.loads(response.text); usage=response.usage_metadata
    payload={'source_start_ms':start,'source_end_ms':end,'chirp_text':chirp_text,**answer,'usage_metadata':usage.model_dump(mode='json') if usage else None}
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(path); print(f'window={index} PASS',flush=True); return payload

def main():
    WORK.mkdir(parents=True,exist_ok=True)
    words=json.loads((RESULTS/'phase3-chirp3-words.json').read_text(encoding='utf-8'))['words']
    windows=[]
    for index,start in enumerate(range(0,300000,30000)):
        end=start+30000; text=''.join(w['word'] for w in words if start<=w['start_ms']<end); windows.append((index,start,end,text))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: corrected=list(pool.map(one,windows))
    payload={'created_at':datetime.now(UTC).isoformat(),'model':os.environ['PHASE2_MODEL'],'windows':corrected}
    (RESULTS/'phase9-gemini-3.6-flash-corrected.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (RESULTS/'phase9-gemini-3.6-flash-corrected.txt').write_text('\n'.join(x['corrected_text'] for x in corrected)+'\n',encoding='utf-8')
    print(f'PHASE9_TEXT=PASS windows={len(corrected)}',flush=True)
if __name__=='__main__': main()
