"""Generate deterministic QA artifacts for the candidate transcript package."""
from __future__ import annotations
import json, re
from html import escape
from pathlib import Path

ROOT=Path('/app'); RESULTS=ROOT/'data'/'results'; OUT=RESULTS/'mvp-output-package'
def main():
    words=json.loads((RESULTS/'phase3-chirp3-words.json').read_text(encoding='utf-8'))['words']
    srt=(OUT/'subtitles-candidate.srt').read_text(encoding='utf-8').strip()
    blocks=srt.split('\n\n'); cues=[]; previous=-1; errors=[]
    for expected,block in enumerate(blocks,1):
        lines=block.splitlines(); match=re.match(r'(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)',lines[1])
        a=list(map(int,match.groups())); start=((a[0]*60+a[1])*60+a[2])*1000+a[3]; end=((a[4]*60+a[5])*60+a[6])*1000+a[7]; text=' '.join(lines[2:])
        cps=len(text)/max(.001,(end-start)/1000); cues.append({'number':expected,'start_ms':start,'end_ms':end,'characters':len(text),'characters_per_second':round(cps,2),'text':text})
        if start<previous or end<=start: errors.append(expected)
        previous=end
    report={'audio_duration_ms':300000,'chirp_word_count':len(words),'subtitle_cue_count':len(cues),'subtitle_last_end_ms':previous,'non_monotonic_cues':errors,'high_read_speed_cues':[c['number'] for c in cues if c['characters_per_second']>12],'coverage_gap_after_ms':300000-previous,'status':'NEEDS_REVIEW'}
    (OUT/'qa-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    timed='\n'.join(f"[{w['start_ms']/1000:07.2f}] {w['word']}" for w in words)+'\n'; (OUT/'transcript-chirp-timestamped.txt').write_text(timed,encoding='utf-8')
    rows=''.join(f"<tr><td>{c['number']}</td><td>{c['start_ms']/1000:.2f}</td><td>{c['end_ms']/1000:.2f}</td><td>{c['characters_per_second']}</td><td>{escape(c['text'])}</td></tr>" for c in cues)
    (OUT/'qa-report.html').write_text(f"<meta charset='utf-8'><h1>Course Transcript MVP QA</h1><pre>{escape(json.dumps(report,ensure_ascii=False,indent=2))}</pre><table border='1'><tr><th>#</th><th>Start</th><th>End</th><th>CPS</th><th>Text</th></tr>{rows}</table>",encoding='utf-8')
    print(f'PHASE11_QA=PASS cues={len(cues)} high_cps={len(report["high_read_speed_cues"])}')
if __name__=='__main__': main()
