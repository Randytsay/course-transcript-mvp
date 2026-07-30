"""Reflow candidate subtitles into shorter readable cues without changing coverage."""
from __future__ import annotations
import re
from pathlib import Path
ROOT=Path('/app'); OUT=ROOT/'data'/'results'/'mvp-output-package'
def parse_ms(value):
 h,m,s,ms=map(int,re.match(r'(\d\d):(\d\d):(\d\d),(\d\d\d)',value).groups()); return ((h*60+m)*60+s)*1000+ms
def stamp(n):
 h,n=divmod(n,3600000); m,n=divmod(n,60000); s,n=divmod(n,1000); return f'{h:02}:{m:02}:{s:02},{n:03}'
def main():
 blocks=(OUT/'subtitles-candidate.srt').read_text(encoding='utf-8').strip().split('\n\n'); cues=[]
 for block in blocks:
  lines=block.splitlines(); a,b=lines[1].split(' --> '); start,end=parse_ms(a),parse_ms(b); text=''.join(lines[2:]).strip()
  pieces=[x for x in re.split(r'(?<=[，。！？；])',text) if x] or [text]
  refined=[]
  for p in pieces: refined += [p[i:i+18] for i in range(0,len(p),18)]
  total=sum(map(len,refined)); used=0
  for i,p in enumerate(refined):
   ps=start+round((end-start)*used/max(1,total)); used+=len(p); pe=end if i==len(refined)-1 else start+round((end-start)*used/max(1,total))
   cues.append((ps,max(ps+1,pe),p))
 text='\n\n'.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n{x}' for i,(a,b,x) in enumerate(cues,1))+'\n'
 (OUT/'subtitles-readable.srt').write_text(text,encoding='utf-8'); (OUT/'subtitles-readable.vtt').write_text('WEBVTT\n\n'+text.replace(',','.'),encoding='utf-8')
 print(f'PHASE12_READABLE=PASS cues={len(cues)} max_chars={max(len(x) for _,_,x in cues)}')
if __name__=='__main__': main()
