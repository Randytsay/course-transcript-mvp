#!/usr/bin/env python3
"""Conservative, source-timestamp-preserving V3 readability candidates.

No paid calls, no Google Drive mutation, no Golden promotion. V2 stays immutable.
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

from scripts.golden24.pilot_preflight import parse_srt, sha256
from scripts.golden24.generate_review_pair import linewrap, tc


def clean(s: str) -> str:
    return re.sub(r"\s+", "", s)


def stats(cues):
    return {
        "cue_count":len(cues),
        "under_500ms":sum(c["end_ms"]-c["start_ms"]<500 for c in cues),
        "under_800ms":sum(c["end_ms"]-c["start_ms"]<800 for c in cues),
        "over_7000ms":sum(c["end_ms"]-c["start_ms"]>7000 for c in cues),
        "over_8cps":sum(
            len(clean(c["text"]))*1000/(c["end_ms"]-c["start_ms"])>8
            for c in cues if c["end_ms"]>c["start_ms"]),
        "line_over_24chars":sum(len(line)>24 for c in cues for line in c["text"].splitlines()),
    }


def merge_score(a, b):
    if a["start_ms"]>=7_000_000 or b["start_ms"]>=7_000_000:
        return None  # Chant cycles and closing ceremonial lines stay unchanged
    gap=b["start_ms"]-a["end_ms"]
    duration=b["end_ms"]-a["start_ms"]
    text=clean(a["text"])+clean(b["text"])
    if not 0<=gap<=500 or not 900<=duration<=6500 or len(text)>39:
        return None
    if len(text)*1000/duration>7.3:
        return None
    wrapped=linewrap(text,24)
    if any(len(line)>24 for line in wrapped.splitlines()) or len(wrapped.splitlines())>2:
        return None
    # Avoid joining two full standalone sentences: only target one very short cue.
    if min(a["end_ms"]-a["start_ms"],b["end_ms"]-b["start_ms"])>=800:
        return None
    preference=0 if a["text"].rstrip().endswith(("，","、","；",":","：")) else 1
    return (gap, preference, abs((len(text)*1000/duration)-4))


def conservative_merge(cues):
    arr=[{"start_ms":c["start_ms"],"end_ms":c["end_ms"],
          "text":c["text"],"source_ids":[c["id"]]} for c in cues]
    actions=[]
    while True:
        chosen=None
        for i,c in enumerate(arr):
            if c["end_ms"]-c["start_ms"]>=800:continue
            options=[]
            if i>0:
                sc=merge_score(arr[i-1],c)
                if sc is not None:options.append((sc,i-1))
            if i+1<len(arr):
                sc=merge_score(c,arr[i+1])
                if sc is not None:options.append((sc,i))
            if options:
                options.sort()
                chosen=options[0][1];break
        if chosen is None:break
        a,b=arr[chosen:chosen+2]
        combined=clean(a["text"])+clean(b["text"])
        merged={"start_ms":a["start_ms"],"end_ms":b["end_ms"],
                "text":linewrap(combined,24),
                "source_ids":a["source_ids"]+b["source_ids"]}
        actions.append({"merged_source_ids":merged["source_ids"],
                        "start_ms":merged["start_ms"],"end_ms":merged["end_ms"],
                        "new_text":merged["text"]})
        arr[chosen:chosen+2]=[merged]
    return arr,actions


def render_txt(cues):
    # Paragraph wrapping only, without fabricated punctuation or changing lexical text.
    paragraphs=[]; buffer=""
    for cue in cues:
        text=clean(cue["text"])
        if not text:continue
        buffer+=text
        if buffer[-1] in "。！？" and len(buffer)>=75:
            paragraphs.append(buffer);buffer=""
        elif len(buffer)>=120:
            paragraphs.append(buffer);buffer=""
    if buffer:paragraphs.append(buffer)
    return "\n\n".join(paragraphs)+"\n"


def build(v2dir:Path, out:Path):
    srt=v2dir/"20260927_Golden_REVIEW.srt"
    txt=v2dir/"20260927_逐字稿_Golden_REVIEW.txt"
    report=json.loads((v2dir/"20260927_Golden_REVIEW_QA.json").read_text())
    if report.get("status")!="REVIEW" or report.get("formal_qa_passed") is not False:
        raise ValueError("Unexpected baseline; V3 accepts V2 REVIEW only")
    if sha256(srt)!=report["candidate_srt_sha256"] or sha256(txt)!=report["candidate_txt_sha256"]:
        raise ValueError("Baseline V2 digest mismatch")
    old, errors=parse_srt(srt)
    if errors:raise ValueError(errors[:5])
    new,changes=conservative_merge(old)
    old_text=clean("".join(c["text"] for c in old))
    new_text=clean("".join(c["text"] for c in new))
    if old_text!=new_text: raise ValueError("Lexical content mismatch after readability merge")
    if len(new)!=len(old)-len(changes):raise ValueError("Merge count mismatch")
    new_srt="\n".join(
        f"{i}\n{tc(c['start_ms'])} --> {tc(c['end_ms'])}\n{c['text']}\n"
        for i,c in enumerate(new,1))+"\n"
    out.mkdir(parents=True,exist_ok=True)
    ps=out/"20260927_Golden_REVIEW_V3.srt"
    pt=out/"20260927_逐字稿_Golden_REVIEW_V3.txt"
    pm=out/"20260927_Golden_REVIEW_V3_QA.json"
    pa=out/"20260927_Golden_REVIEW_V3_readability_edits.json"
    for path,data in [(ps,new_srt),(pt,render_txt(new))]:
        if path.exists() and path.read_text()!=data:
            raise ValueError(f"Existing V3 file collision: {path}")
        path.write_text(data)
    check,issues=parse_srt(ps)
    if issues:raise ValueError(f"V3 SRT invalid: {issues[:5]}")
    if clean(pt.read_text())!=new_text:
        raise ValueError("V3 SRT/TXT lexical difference")
    if clean(txt.read_text())!=new_text:
        raise ValueError("V3 removed any V2 text")
    # Known unresolved acoustic/content defects remain explicit blockers.
    historic=json.loads((v2dir/"20260927_Golden_REVIEW_QA.json").read_text())
    data={
        "schema_version":"golden24-v3-readability-v1",
        "source_date":"20260927",
        "status":"REVIEW",
        "publish_allowed":False,
        "v2_source_srt_sha256":sha256(srt),
        "v2_source_txt_sha256":sha256(txt),
        "v3_srt_sha256":sha256(ps),
        "v3_txt_sha256":sha256(pt),
        "v2_readability":stats(old),
        "v3_readability":stats(check),
        "merge_actions":len(changes),
        "all_time_boundaries_from_v2":True,
        "no_chirp_timestamps_invented":True,
        "lexical_text_unchanged_from_v2":True,
        "chant_start_words_preserved":old_text.count("南謨囉怛那怛囉夜耶")==new_text.count("南謨囉怛那怛囉夜耶"),
        "max_txt_paragraph":max(map(len,pt.read_text().split("\n\n"))),
        "blocking_items":[
            "independent full audio VAD speech-coverage proof not completed",
            "previous 3 human_missing allegations must be reconciled against original audio",
            "2 fallback stretches contain unpunctuated ASR text and possible word errors",
            "residual short/long/fast readability anomalies need handling or documented exceptions",
            "audio tail must be classified speech vs applause/noise",
            "source timing provenance must be checked against every changed cue",
        ],
        "formal_drive_overwrite":False,
        "original_v2_baseline_unchanged":True
    }
    pm.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    pa.write_text(json.dumps(changes,ensure_ascii=False,indent=2)+"\n")
    return data


if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("--v2",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    opt=a.parse_args()
    print(json.dumps(build(opt.v2,opt.out),ensure_ascii=False,indent=2))
