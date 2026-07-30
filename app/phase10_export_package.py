"""Assemble a reviewable MVP output package without changing source evidence."""
from __future__ import annotations
import csv, json, shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path('/app'); RESULTS=ROOT/'data'/'results'; OUT=RESULTS/'mvp-output-package'

def main() -> int:
    OUT.mkdir(parents=True,exist_ok=True)
    corrected=json.loads((RESULTS/'phase9-gemini-3.6-flash-corrected.json').read_text(encoding='utf-8'))
    alignment=json.loads((RESULTS/'phase7-alignment-qa.json').read_text(encoding='utf-8'))
    terms=[]
    for window_index,window in enumerate(corrected['windows']):
        for term in window['uncertain_terms']:
            terms.append({'window_index':window_index,'start_ms':window['source_start_ms'],'end_ms':window['source_end_ms'],'candidate':term,'decision':''})
    shutil.copy2(RESULTS/'phase9-gemini-3.6-flash-corrected.txt',OUT/'transcript-corrected.txt')
    shutil.copy2(RESULTS/'phase6-gemini-3.6-flash-microchunks.txt',OUT/'transcript-gemini-raw.txt')
    shutil.copy2(RESULTS/'phase7-gemini-3.6-flash-aligned.srt',OUT/'subtitles-candidate.srt')
    srt=(OUT/'subtitles-candidate.srt').read_text(encoding='utf-8')
    (OUT/'subtitles-candidate.vtt').write_text('WEBVTT\n\n'+srt.replace(',', '.'),encoding='utf-8')
    with (OUT/'term-review.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['window_index','start_ms','end_ms','candidate','decision']); writer.writeheader(); writer.writerows(terms)
    report={'created_at':datetime.now(UTC).isoformat(),'model':'gemini-3.6-flash','audio_duration_ms':300000,'chirp_word_count':1210,'corrected_character_count':sum(len(w['corrected_text']) for w in corrected['windows']),'candidate_srt_cues':alignment['cue_count'],'alignment_match_ratio_by_window':[w['match_ratio'] for w in alignment['chunks']],'fallback_segment_count':alignment['fallback_segment_count'],'unresolved_term_count':len(terms),'status':'NEEDS_REVIEW'}
    (OUT/'qa-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'manifest.json').write_text(json.dumps({'created_at':datetime.now(UTC).isoformat(),'files':sorted(p.name for p in OUT.iterdir()),'status':'NEEDS_REVIEW'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'PHASE10_EXPORT=PASS files={len(list(OUT.iterdir()))}')
if __name__=='__main__': main()
