#!/usr/bin/env python3
"""V4 *display* synchronization: optional anticipatory lead and short hold.

V3 words, speech timing anchors, and Google Drive formal files are immutable.
Never rewrites SRT text, never calls ASR or any external service, and cannot publish.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from scripts.golden24.pilot_preflight import parse_srt, sha256
from scripts.golden24.generate_review_pair import tc

MAX_LEAD_MS=450
MAX_HOLD_MS=220

def clean(text:str)->str:
    return re.sub(r"\s+","",text)

def pad_display_timing(src:list[dict])->tuple[list[dict],list[dict]]:
    if not src:
        raise ValueError("No V3 input cues")
    out=[{"start_ms":int(x["start_ms"]), "end_ms":int(x["end_ms"]),
          "speech_start_ms":int(x["start_ms"]), "speech_end_ms":int(x["end_ms"]),
          "text":x["text"], "source_id":x["id"]} for x in src]
    actions=[]
    # Allocate every positive original silence gap between the prior cue hold and
    # next cue early display. Original speech boundaries remain as provenance.
    for idx in range(1,len(out)):
        previous,current=out[idx-1],out[idx]
        gap=current["speech_start_ms"]-previous["speech_end_ms"]
        if gap < 0:
            raise ValueError(f"Overlapping original cues near {idx}")
        if gap == 0:
            continue
        # Pause-length still matters: don't fill a several-second genuine pause.
        # Give most of the small gap to EARLY DISPLAY, with a shorter hold to
        # avoid the old phrase vanishing immediately as its last word ends.
        hold=min(MAX_HOLD_MS, int(gap*0.24))
        lead=min(MAX_LEAD_MS, gap-hold)
        if lead < 0 or hold < 0 or (lead+hold)>gap:
            raise ValueError("Display gap budget invalid")
        if hold:
            previous["end_ms"] += hold
        if lead:
            current["start_ms"] -= lead
        if current["start_ms"]<previous["end_ms"]:
            raise ValueError("Display captions overlap")
        if lead or hold:
            actions.append({
                "previous_source_id":previous["source_id"],
                "current_source_id":current["source_id"],
                "gap_ms":gap,
                "previous_hold_ms":hold,
                "next_early_display_ms":lead,
                "new_previous_end_ms":previous["end_ms"],
                "new_current_start_ms":current["start_ms"],
                "original_current_speech_start_ms":current["speech_start_ms"],
            })
    for x in out:
        if x["start_ms"]<0 or x["end_ms"]<=x["start_ms"]:
            raise ValueError("Invalid padded SRT")
        if x["start_ms"]>x["speech_start_ms"] or x["end_ms"]<x["speech_end_ms"]:
            raise ValueError("Padded display must cover original speech window")
    return out,actions

def make(v3:Path,output:Path)->dict:
    qa_path=v3/"20260927_Golden_REVIEW_V3_QA.json"
    q=json.loads(qa_path.read_text(encoding="utf8"))
    src_path=v3/"20260927_Golden_REVIEW_V3.srt"
    txt_path=v3/"20260927_逐字稿_Golden_REVIEW_V3.txt"
    if q.get("status")!="REVIEW" or q.get("publish_allowed") is not False:
        raise ValueError("V3 is unexpectedly not an immutable REVIEW candidate")
    if sha256(src_path)!=q["v3_srt_sha256"] or sha256(txt_path)!=q["v3_txt_sha256"]:
        raise ValueError("V3 source SHA mismatch")
    src,issues=parse_srt(src_path)
    if issues:raise ValueError(issues[:5])
    out,changes=pad_display_timing(src)
    body="\n".join(f"{i}\n{tc(x['start_ms'])} --> {tc(x['end_ms'])}\n{x['text']}\n"
                   for i,x in enumerate(out,1))+"\n"
    output.mkdir(parents=True,exist_ok=True)
    sp=output/"20260927_Golden_REVIEW_V4_提前顯示.srt"
    tp=output/"20260927_逐字稿_Golden_REVIEW_V4.txt"
    qc=output/"20260927_Golden_REVIEW_V4_QA.json"
    ea=output/"20260927_Golden_REVIEW_V4_時間調整.json"
    if sp.exists() and sp.read_text(encoding="utf8")!=body:
        raise ValueError("Existing V4 candidate collision")
    sp.write_text(body,encoding="utf8")
    # Transcript content is fully unchanged; copy it for a standalone SRT/TXT acceptance pair.
    source_txt=txt_path.read_bytes()
    if tp.exists() and tp.read_bytes()!=source_txt:
        raise ValueError("Existing V4 TXT collision")
    tp.write_bytes(source_txt)
    rec,errs=parse_srt(sp)
    if errs:raise ValueError(errs[:5])
    if len(rec)!=len(src):raise ValueError("Cue count changed")
    if clean("".join(x["text"] for x in rec))!=clean("".join(x["text"] for x in src)):
        raise ValueError("Text changed")
    if clean(txt_path.read_text(encoding="utf8"))!=clean("".join(x["text"] for x in rec)):
        raise ValueError("Review TXT no longer matches SRT")
    srt_gaps=lambda x:sum(b["start_ms"]>a["end_ms"] for a,b in zip(x,x[1:]))
    lengths=[c["duration_ms"] for c in rec]
    nlead=sum(1 for i in range(len(src)) if rec[i]["start_ms"]<src[i]["start_ms"])
    nhold=sum(1 for i in range(len(src)) if rec[i]["end_ms"]>src[i]["end_ms"])
    all_clamped=all(rec[i]["start_ms"]<=src[i]["start_ms"] and rec[i]["end_ms"]>=src[i]["end_ms"]
                    for i in range(len(src)))
    if not all_clamped:
        raise ValueError("Start/end display padding invariants failed")
    summary={
       "schema_version":"golden24-v4-display-timing-v1",
       "status":"REVIEW",
       "publish_allowed":False,
       "purpose":"early display cue at sentence onset and hold through the end",
       "v3_srt_sha256":sha256(src_path),
       "v3_txt_sha256":sha256(txt_path),
       "v4_srt_sha256":sha256(sp),
       "cue_count":len(rec),
       "early_display_limit_ms":MAX_LEAD_MS,
       "after_speech_hold_limit_ms":MAX_HOLD_MS,
       "cues_early_started":nlead,
       "cues_held_later":nhold,
       "gaps_before":srt_gaps(src),
       "gaps_after":srt_gaps(rec),
       "no_overlaps":True,
       "original_speech_boundaries_preserved_in_change_log":True,
       "candidate_lexical_content_identical_to_v3":True,
       "v4_txt_sha256":sha256(tp),
       "corresponding_txt":"20260927_逐字稿_Golden_REVIEW_V4.txt",
       "independent_audio_alignment_passed":False,
       "player_render_delay_ruled_out":False,
       "unresolved_acoustic_qa_from_v3":q["blocking_items"],
       "formal_drive_file_modified":False,
    }
    qc.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    ea.write_text(json.dumps(changes,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    return summary

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--v3",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    a=p.parse_args()
    print(json.dumps(make(a.v3,a.out),ensure_ascii=False,indent=2))
