#!/usr/bin/env python3
"""Generate a self-consistent 20260927 SRT/TXT REVIEW bundle from source-verified evidence.

Not a release; never calls Drive or a paid provider.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

from scripts.golden24.pilot_preflight import parse_srt, sha256, verify_snapshot
from scripts.golden24.chirp_word_coverage import check_chirp_coverage

JOB_ID="20260927-20260928-154344-1bbe8d"

def tc(ms:int)->str:
    h,r=divmod(ms,3600000);m,r=divmod(r,60000);s,k=divmod(r,1000)
    return f"{h:02d}:{m:02d}:{s:02d},{k:03d}"

def cjktypography(t:str)->str:
    return t.replace(",", "，").replace("?", "？").replace("!", "！")

def linewrap(text:str,limit:int=24)->str:
    # Split the display text across two lines, retaining the exact characters.
    s=text.replace("\n","")
    if len(s)<=limit:return s
    points=[i+1 for i,c in enumerate(s[:-1]) if c in "，；、。？！： "]
    candidates=[i for i in points if 6<=i<=len(s)-5]
    if not candidates:return s
    split=min(candidates,key=lambda i:(max(i,len(s)-i),abs(i-len(s)/2)))
    return s[:split]+"\n"+s[split:]

def in_span(c,lo,hi):return c["start_ms"]>=lo and c["end_ms"]<=hi

def transform(backup:Path,job:Path,outdir:Path)->dict:
    provenance=verify_snapshot(backup)
    if not provenance["verified"]:raise ValueError("Backup digest mismatch")
    source=json.loads((job/"source-manifest.json").read_text())
    if source.get("source_sha256")!=provenance["sha256"]["20260927.MP3"]:
        raise ValueError("Chirp provenance mismatch")
    a=json.loads((job/"transcript.json").read_text())
    if not str(a.get("source_timing","")).startswith("chirp_3"):
        raise ValueError("Missing Chirp 3 timestamp provenance")
    display=a["display_segments"]
    old,errs=parse_srt(backup/"20260927.srt")
    if errs:raise ValueError(errs[:3])
    edits=[]
    # 1. omitted short word-timed phrase at the 64-billion Henghe-sand discussion
    phrase=[x for x in display if x["start_ms"]==1180080 and x["end_ms"]==1182680]
    if len(phrase)!=1: raise ValueError("64億 anchor not exact")
    edits.append({"kind":"add","start_ms":1180080,"end_ms":1182680,
                  "text":"遍虛空啊，遍等於是遍虛空，",
                  "provenance":"chirp display matched, manually typography normalized"})
    # Move the overlapping next title to the real display boundary (same text).
    old_next=[x for x in old if x["start_ms"]==1182360]
    if len(old_next)!=1 or old_next[0]["text"]!="發阿耨多羅三藐三菩提心，":
        raise ValueError("64億 following cue changed")
    old_next[0]["start_ms"]=1183000
    # 2. third assembly lecture: preserve spoken transition previously omitted;
    # maintain old 34億 cue which represents an actual subsequent spoken repetition.
    for x in display:
        if 1693680<=x["start_ms"] and x["end_ms"]<=1703600 and x["text"].strip():
            text=cjktypography(x["text"].strip())
            if x["start_ms"]==1693680:
                text="三十四億天龍八部發三菩提心。當時會下"
            elif x["start_ms"]==1696760:
                text="九十四億人圓滿"
            elif x["start_ms"]==1699040:
                text="了，再接下去就是九十二億了。"
            edits.append({"kind":"add","start_ms":x["start_ms"],
                          "end_ms":x["end_ms"],"text":text,
                          "provenance":"Chirp-timed existing display, spoken 34億 repeated at 1703880"})
    # 3. gap at 01:41:31 about transformed celestial ornaments.
    # New text selected from source meanings, not inflated canonical scripture.
    for st,en,text in [
        (6091200,6095040,"因此就變化聖物了。"),
        (6095960,6098120,"剛剛說過的是諸天，"),
        (6098160,6100560,"天瓔珞跟天衣，"),
        (6100600,6103920,"是指天子、天女身上的東西變化出來，"),
        (6103920,6105800,"就供養佛。")]:
        edits.append({"kind":"add","start_ms":st,"end_ms":en,"text":text,
                      "provenance":"time from Chirp display; edited oral terms REVIEW"})
    # 4. old formal SRT misses a spoken reintroduction to a scripture quotation.
    for st,en,text in [
        (6622080,6625760,"好，接下去：「世尊入城時，"),
        (6625800,6628360,"大梵天王、釋提桓因，"),
        (6628400,6630320,"合掌恭敬，以偈讚佛。」")]:
        edits.append({"kind":"add","start_ms":st,"end_ms":en,"text":text,
                      "provenance":"Chirp display and raw words; no unspoken full verse insertion"})
    # 5. continuous 83-second blank plus misplaced cues after its end.
    # Replace until next exactly matching human SRT at 5335320. Chirp timing
    # and corrected historical display text replace stale time-aligned human cues.
    lo,hi=5236120,5335320
    removed=[x for x in old if lo<=x["start_ms"]<hi]
    if not removed or removed[0]["start_ms"]!=5319560:
        raise ValueError("Unexpected existing 83s-gap boundary")
    region=[x for x in display if lo<=x["start_ms"] and x["end_ms"]<=hi and x.get("text","").strip()]
    if len(region)<30 or region[0]["start_ms"]!=5238120 or region[-1]["end_ms"]!=5334360:
        raise ValueError("Historic display source changed")
    # This source includes a semantic passage shifted in prior SRT; no duplication.
    for x in region:
        edits.append({"kind":"add","start_ms":x["start_ms"],"end_ms":x["end_ms"],
                      "text":cjktypography(x["text"].strip()),
                      "provenance":"prior corrected Chirp 3 timed display segment"})
    remaining=[x.copy() for x in old if x not in removed]
    # Full restriction: never invent word timestamps for additional stretches.
    combined=remaining+[{"start_ms":x["start_ms"],"end_ms":x["end_ms"],
                         "text":x["text"],"origin":"restored"} for x in edits]
    combined.sort(key=lambda x:(x["start_ms"],x["end_ms"]))
    # Add wrapped lines but no new punctuation on unedited original cues.
    chunks=[]
    for i,x in enumerate(combined,1):
        chunks.append(f"{i}\n{tc(x['start_ms'])} --> {tc(x['end_ms'])}\n{linewrap(x['text'])}\n")
    srt="\n".join(chunks)+"\n"
    outdir.mkdir(parents=True,exist_ok=True)
    sp=outdir/"20260927_Golden_REVIEW.srt"
    tp=outdir/"20260927_逐字稿_Golden_REVIEW.txt"
    if sp.exists() and sp.read_text()!=srt:
        raise ValueError("Review SRT already exists with different bytes")
    sp.write_text(srt,encoding="utf-8")
    cues,errors=parse_srt(sp)
    if errors:raise ValueError(f"bad output cues {errors[:5]}")
    # Consistency-first full transcript. Paragraphs are reflow only:
    # concatenated non-whitespace lexical content MUST equal SRT cue content.
    full="".join(x["text"].replace("\n","") for x in cues)
    pieces=re.split(r"(?<=[。！？])",full)
    paragraphs=[];buf=""
    for piece in pieces:
        if not piece:continue
        if len(buf)+len(piece)>140 and buf:
            paragraphs.append(buf)
            buf=""
        buf+=piece
        if len(buf)>=95 and (buf[-1] in "。！？"):
            paragraphs.append(buf);buf=""
    if buf:paragraphs.append(buf)
    txt="\n\n".join(paragraphs).strip()+"\n"
    if tp.exists() and tp.read_text()!=txt:
        raise ValueError("Review TXT already exists with different bytes")
    tp.write_text(txt,encoding="utf-8")
    strip=lambda s:re.sub(r"\s+","",s)
    if strip(txt)!=strip(full):raise ValueError("TXT no longer character identical to SRT")
    old_cov=check_chirp_coverage(backup,job,old,provenance["sha256"]["20260927.MP3"])
    new_cov=check_chirp_coverage(backup,job,cues,provenance["sha256"]["20260927.MP3"])
    keep_metrics=lambda c:{
        "count":len(c),
        "under_500ms":sum(x["duration_ms"]<500 for x in c),
        "over_7s":sum(x["duration_ms"]>7000 for x in c),
        "over_8cps":sum(len(strip(x["text"]))*1000/x["duration_ms"]>8 for x in c if x["duration_ms"]>0),
        "lines_over_24chars":sum(len(line)>24 for x in c for line in x["text"].split("\n")),
        "gap_over_30s":sum(b["start_ms"]-a["end_ms"]>30000 for a,b in zip(c,c[1:]))
    }
    old_metrics=keep_metrics(parse_srt(backup/"20260927.srt")[0])
    new_metrics=keep_metrics(cues)
    hist=json.loads((backup/"20260927_transcript_report.json").read_text())
    issues={
        "human_missing":hist["qa"]["human_missing"],
        "historical_split_candidates":hist["qa"]["boundary_integrity"]["split_candidate_count"],
        "historical_unclear_boundaries":hist["qa"]["boundary_integrity"]["close_unpunctuated_boundary_count"],
    }
    summary={
        "schema_version":"golden24-review-candidate-v2",
        "lesson_date":"20260927",
        "status":"REVIEW",
        "publish_allowed":False,
        "source_mp3_sha256":provenance["sha256"]["20260927.MP3"],
        "original_srt_sha256":provenance["sha256"]["20260927.srt"],
        "original_txt_sha256":provenance["sha256"]["20260927_逐字稿.txt"],
        "candidate_srt_sha256":sha256(sp),
        "candidate_txt_sha256":sha256(tp),
        "candidate_text_character_identity":True,
        "original_cues":old_metrics,
        "candidate_cues":new_metrics,
        "old_uncovered_chirp_words_screen":old_cov["uncovered_chirp_words"],
        "candidate_uncovered_chirp_words_screen":new_cov["uncovered_chirp_words"],
        "unresolved_significant_uncovered_runs":new_cov["significant_uncovered_top20"],
        "operations":{"new_cues_added":len(edits),"source_cues_removed_for_alignment":len(removed),
                      "source_cues_time_shifted":1,"material_change_windows_ms":[
                        [1162360,1185120],[1692880,1707960],[6090800,6105840],
                        [6620000,6633000],[5236120,5335320]]},
        "source_qa_issues":issues,
        "human_acoustic_review_required":True,
        "full_audio_vad_coverage_verified":False,
        "no_full_audio_retranscription":True,
        "unverified_edited_technical_terminology_windows_ms":[[6091200,6105800],[6622080,6630320]],
        "original_drive_files_modified":False,
        "formal_qa_passed":False,
    }
    (outdir/"20260927_Golden_REVIEW_QA.json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    (outdir/"20260927_Golden_REVIEW_edits.json").write_text(
        json.dumps(edits,ensure_ascii=False,indent=2)+"\n")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshot",required=True,type=Path)
    p.add_argument("--chirp-job",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    a=p.parse_args()
    x=transform(a.snapshot,a.chirp_job,a.out)
    print(json.dumps({k:v for k,v in x.items() if k in ("status","candidate_text_character_identity","original_cues","candidate_cues","operations","old_uncovered_chirp_words_screen","candidate_uncovered_chirp_words_screen")},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
