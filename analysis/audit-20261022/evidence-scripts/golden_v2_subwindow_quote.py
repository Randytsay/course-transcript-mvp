"""Count tokens for the 3 replacement sub-windows of the truncated 261017 window.

Container-aware: source root /input (read-only), output root /output (writable).
countTokens only - no generation, no provider spend.
"""
import hashlib,json,os,subprocess
from pathlib import Path
from decimal import Decimal,ROUND_CEILING
from google import genai
from google.genai import types
import google.auth

JID='v2p_62a124bdd4595aca91c53085c3732ca7'
SRC=Path(os.environ.get('SRC_ROOT','/input'))/'jobs'/JID/'normalized.flac'
OUT=Path(os.environ.get('OUT_ROOT','/output'))
SUB=[(2690000,3600000),(3590000,4500000),(4490000,5390000)]
PROMPT=('請完整聆聽提供的錄音，依原始播放順序輸出完整繁體中文逐字稿。不摘要、不省略可辨識的口語、重複、聽眾回應或中途改述。'
        '辨認國語與台語混用，保留說話者原意。有經文時謹慎核對語音：只有真的聽到的內容才可寫入，不可用熟悉的經文補上沒有朗讀的字句。'
        '以自然標點與段落呈現，不附帶解釋，不生成時間碼。無法辨識處請明確標示〔聽不清楚〕，絕對不可猜測。')
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
clips=[]
for i,(s,e) in enumerate(SUB):
    p=OUT/f'subwindow-{i}-{s}-{e}.flac'
    if not p.exists() or p.stat().st_size==0:
        r=subprocess.run(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-ss',f'{s/1000:.3f}','-i',str(SRC),'-t',f'{(e-s)/1000:.3f}','-ac','1','-ar','16000','-c:a','flac',str(p)],capture_output=True,timeout=300)
        if r.returncode!=0:
            raise SystemExit('ffmpeg failed: '+r.stderr.decode()[:400])
    dur=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(p)]))
    clips.append({'index':i,'start_ms':s,'end_ms':e,'path':str(p),'sha256':sha(p),'duration_seconds':dur,'bytes':p.stat().st_size})
client=genai.Client(enterprise=True,project=os.environ.get('GOOGLE_CLOUD_PROJECT','xenon-chain-506409-c3'),location='global',http_options=types.HttpOptions(api_version='v1',timeout=60000,retry_options=types.HttpRetryOptions(attempts=1)))
MODEL='gemini-3.8-flash'
MAX_OUT=16384
IN_PRICE=Decimal('0.75');OUT_PRICE=Decimal('3.75')
for c in clips:
    contents=[types.Content(role='user',parts=[types.Part.from_bytes(data=Path(c['path']).read_bytes(),mime_type='audio/flac'),types.Part.from_text(text=PROMPT)])]
    counted=client.models.count_tokens(model=MODEL,contents=contents)
    tokens=counted.total_tokens
    assert type(tokens) is int and tokens>0
    c['input_tokens']=tokens
    c['cached_content_token_count']=counted.cached_content_token_count
    c['max_output_tokens']=MAX_OUT
# Worst case: every request burns the full output cap (what the ledger must reserve).
worst=sum((Decimal(c['input_tokens'])*IN_PRICE+Decimal(MAX_OUT)*OUT_PRICE)/1000000 for c in clips)
# Realistic: the truncated original produced 3,600 chars over 2,700s of audio.
ORIG_CHARS=3600;ORIG_SEC=2700
est_out=[min(MAX_OUT,int(ORIG_CHARS*(c['end_ms']-c['start_ms'])/1000/ORIG_SEC)) for c in clips]
realistic=sum((Decimal(c['input_tokens'])*IN_PRICE+Decimal(o)*OUT_PRICE)/1000000 for c,o in zip(clips,est_out))
def ce(u):return int((u*100).to_integral_value(rounding=ROUND_CEILING))
summary={'schema':'golden-v2-primary-subwindow-quote-v1','job_id':JID,'model':MODEL,
 'max_output_tokens_per_request':MAX_OUT,'thinking_level':'LOW',
 'clips':clips,'total_input_tokens':sum(c['input_tokens'] for c in clips),
 'worst_case_usd':str(worst),'worst_case_cents':ce(worst),
 'estimated_output_tokens_per_clip':est_out,
 'realistic_usd':str(realistic),'realistic_cents':ce(realistic),
 'unused_gemini_reservation_cents':147,'approved_ceiling_cents':261,'recorded_actual_cents':114,
 'fits_within_unused_reservation':ce(worst)<=147,
 'price_version':'google-list-global-20261014','invoice_verified':False,
 'provider_generation_calls_started':0,'ledger_mutations':0,'drive_mutations':0}
(OUT/'subwindow-quote.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
client.close()
