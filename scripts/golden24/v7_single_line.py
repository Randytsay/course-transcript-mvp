#!/usr/bin/env python3
"""20260927 V7: single-line *timed* subtitle candidate with fail-closed QA.

Never renders a newline inside an SRT cue. Split a V6 visually wrapped cue only
where V2 or independently preserved Chirp-3 word tokens prove the boundary.
Unresolved cues are kept as ONE unsplit single-line cue and marked as release
blockers; NEVER silently omit words or fabricate timestamp interpolation.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from scripts.golden24.pilot_preflight import parse_srt, sha256
from scripts.golden24.generate_review_pair import tc
from scripts.golden24.v5_semantic_layout import lex
from scripts.golden24.v6_word_boundary import flatten,PROTECTED,token_boundaries
from app.providers.subtitle_single_line_gate import strict_srt_gate

# A boundary can occur only at the start of a *word* in preserved Chirp 3.
# Require BOTH preceding and following speech content matched uniquely.
MIN_WORD_DURATION=480
SINGLE_LINE_HARD_TARGET=16

# Domain compounds and regular grammatical words are always kept intact.
# A Chirp token start is NOT sufficient evidence to separate such a phrase.
ADDITIONAL_PROTECTED=(
    "鳩摩羅什","未來","大慈","大悲","不生不滅","有辦法","翅頭末城",
    "翅頭末","三藐三菩提","阿耨多羅三藐三菩提心","六種神通",
    "八萬四千","三十二輔臣","聲聞菩提","彌勒菩薩","大乘佛法",
    "覺悟","成佛","有緣","羅漢","八部眾","十八種神變",
    "沒有辦法","無量淨居天眾","淨居天眾","有沒有辦法",
    "世間人","佛弟子","龍華三會","恒河沙","恆河沙",
)
NO_SPLIT_TERMS=tuple(dict.fromkeys(PROTECTED+ADDITIONAL_PROTECTED))

def safe_phrase_boundary(before:str,after:str):
    whole=before+after
    idx=len(before)
    boundaries=token_boundaries(whole)
    if idx not in boundaries:
        return False,{"reason":"word-segmentation-crosses-boundary",
                      "left":before[-12:],"right":after[:12]}
    clean=lex(whole)
    cut=len(lex(before))
    for term in NO_SPLIT_TERMS:
        needle=lex(term)
        first=0
        while needle:
            at=clean.find(needle,first)
            if at<0:break
            if at<cut<at+len(needle):
                return False,{"reason":"protected-lexeme-crosses-boundary",
                              "term":term,"left":before[-14:],"right":after[:14]}
            first=at+1
    return True,{"reason":"whole-phrase-token-boundary"}

def paired_chirp_boundary(before:str,after:str, words:list[dict],start_ms:int,end_ms:int):
    left=lex(before);right=lex(after)
    if len(left)<3 or len(right)<3:
        return None,{"reason":"too-short-phrase","left_len":len(left),"right_len":len(right)}
    local=[w for w in words if start_ms-160<=int(w["start_ms"])<end_ms+160]
    chars=[];timestamps=[];word_starts=set()
    for word in local:
        content=lex(str(word["word"]))
        if not content:continue
        word_starts.add(len(chars))
        chars.extend(content)
        timestamps.extend([int(word["start_ms"])]*len(content))
    speech="".join(chars)
    # An independent verification of two separate lexical anchors. A candidate
    # needs >=4 characters after the split and >=3 before it (with at most
    # two *extra spoken* filler chars between these anchors).
    options=[]
    for right_len in range(min(len(right),10),3,-1):
        seek=right[:right_len]
        p=0
        while True:
            j=speech.find(seek,p)
            if j<0:break
            p=j+1
            if j not in word_starts:continue
            t=timestamps[j]
            if not (start_ms+MIN_WORD_DURATION<t<end_ms-MIN_WORD_DURATION):
                continue
            for left_len in range(min(len(left),9),2,-1):
                tail=left[-left_len:]
                distance=None
                for gap in range(0,3):
                    if j-left_len-gap>=0 and speech[j-left_len-gap:j-gap if gap else j]==tail:
                        distance=gap
                        break
                if distance is not None:
                    options.append({"ms":t,"left_anchor":tail,"right_anchor":seek,
                                    "spoken_filler_chars":distance,
                                    "support_chars":left_len+right_len})
                    break
    if not options:
        return None,{"reason":"no-dual-sided-Chirp-anchor",
                     "left_suffix":left[-12:],"right_prefix":right[:12],
                     "word_tokens":len(local)}
    # Check timestamp uniqueness regardless of anchor lengths. If the right
    # phrase occurs twice, there is no acceptable unique position.
    distinct={o["ms"] for o in options}
    if len(distinct)!=1:
        return None,{"reason":"ambiguous-audio-word-occurrence",
                     "candidate_times_ms":sorted(distinct)}
    best=sorted(options,key=lambda x:(-x["support_chars"],x["spoken_filler_chars"]))[0]
    return best["ms"],{"reason":"two-sided-unique-Chirp-3-word-anchor",**best}

def v2_offset_timings(v2:list[dict]):
    offsets={}
    cursor=0
    for c in v2:
        offsets.setdefault(cursor,c["start_ms"])
        cursor+=len(lex(flatten(c["text"])))
    return offsets,cursor

def split_with_proof(lines:list[str],start:int,end:int,global_offset:int,source_v2:dict,words:list[dict]):
    if not lines or len(lines)==1:
        return [],[]
    decisions=[];cursor=global_offset
    for i,left in enumerate(lines[:-1]):
        cursor+=len(lex(left))
        approved,word_gate=safe_phrase_boundary(left,lines[i+1])
        if not approved:
            return [],[{"line_boundary":i,"details":word_gate}]
        boundary=source_v2.get(cursor)
        if (boundary is not None and start+MIN_WORD_DURATION<boundary<end-MIN_WORD_DURATION):
            decisions.append({"index":i,"boundary_ms":boundary,
                              "source":"original-V2-cue-start","offset":cursor})
            continue
        # Fallback to dual-audio anchor. Do not infer timestamp from char ratios.
        at,details=paired_chirp_boundary(left,lines[i+1],words,start,end)
        if at is None:
            return [],[{"line_boundary":i,"details":details}]
        decisions.append({"index":i,"boundary_ms":at,
                          "source":"Chirp-3-two-sided-unique-word-match","details":details})
    times=[start]+[x["boundary_ms"] for x in decisions]+[end]
    if any(b-a<MIN_WORD_DURATION for a,b in zip(times,times[1:])):
        return [],[{"reason":"insufficient-display-time-per-segment",
                    "boundaries_ms":times}]
    if any(b<=a for a,b in zip(times,times[1:])):
        return [],[{"reason":"nonmonotonic-chirp-timing","boundaries_ms":times}]
    return decisions,[]


def run(v6dir:Path,v2dir:Path,jobdir:Path,out:Path):
    v6path=v6dir/"20260927_Golden_REVIEW_V6_詞組時間校正.srt"
    v2path=v2dir/"20260927_Golden_REVIEW.srt"
    a=json.loads((v6dir/"20260927_Golden_REVIEW_V6_QA.json").read_text())
    b=json.loads((v2dir/"20260927_Golden_REVIEW_QA.json").read_text())
    manifest=json.loads((jobdir/"source-manifest.json").read_text())
    if a.get("publish_allowed") is not False or a["candidate_srt_sha256"]!=sha256(v6path):
        raise ValueError("V6 integrity/REVIEW provenance failed")
    if b["candidate_srt_sha256"]!=sha256(v2path):
        raise ValueError("V2 SHA source proof failed")
    if manifest.get("source_sha256")!=a.get("source_chirp_sha256"):
        raise ValueError("Chirp audio provenance fails")
    cues,err=parse_srt(v6path)
    prev,err2=parse_srt(v2path)
    if err or err2:raise ValueError("Source SRT invalid")
    if lex("".join(c["text"] for c in cues))!=lex("".join(c["text"] for c in prev)):
        raise ValueError("V2/V6 textual provenance differs")
    timings,total=v2_offset_timings(prev)
    words=json.loads((jobdir/"merged-words.json").read_text())["words"]
    stage=[];splits=[];blocked=[]
    cursor=0
    for orig in cues:
        lines=orig["text"].splitlines()
        previous_offset=cursor
        cursor+=len(lex("".join(lines)))
        if len(lines)==1:
            stage.append({"start_ms":orig["start_ms"],"end_ms":orig["end_ms"],
                          "text":lines[0]})
            continue
        decisions,fail=split_with_proof(lines,orig["start_ms"],orig["end_ms"],
                                        previous_offset,timings,words)
        if fail:
            # Never embed two-line subtitles into candidate SRT; do not
            # fabricate a boundary. Keep one long cue for explicit QA block.
            stage.append({"start_ms":orig["start_ms"],"end_ms":orig["end_ms"],
                          "text":"".join(lines)})
            blocked.append({"source_cue_id":orig["id"],
                            "start_ms":orig["start_ms"],"end_ms":orig["end_ms"],
                            "original_visual_lines":lines,
                            "failure":fail})
            continue
        ticks=[orig["start_ms"]]+[d["boundary_ms"] for d in decisions]+[orig["end_ms"]]
        for text,st,en in zip(lines,ticks,ticks[1:]):
            stage.append({"start_ms":st,"end_ms":en,"text":text})
        splits.append({"source_cue_id":orig["id"],"original_time":[orig["start_ms"],orig["end_ms"]],
                       "new_segments":lines,"boundary_proof":decisions})
    if cursor!=total:raise ValueError("Source lexical offset is incomplete")
    if lex("".join(c["text"] for c in stage))!=lex("".join(c["text"] for c in cues)):
        raise ValueError("V7 content loss")
    if any(x["start_ms"]>=x["end_ms"] for x in stage):raise ValueError("Bad cue duration")
    if any(y["start_ms"]<x["end_ms"] for x,y in zip(stage,stage[1:])):
        raise ValueError("Audio time overlaps after V7 split")
    # New release rule: the SRT candidate must *always* be one-line. An
    # incomplete candidate does not make the entire track release-ready.
    gate=strict_srt_gate(stage,unresolved=blocked,require_unresolved_zero=True)
    if gate["multiline_cues"]!=0:raise ValueError("V7 single-line invariant broken")
    output="\n".join(f"{i}\n{tc(c['start_ms'])} --> {tc(c['end_ms'])}\n{c['text']}\n"
                     for i,c in enumerate(stage,1))+"\n"
    out.mkdir(parents=True,exist_ok=True)
    filename=out/"20260927_Golden_REVIEW_V7_單行聲學配時.srt"
    txt=out/"20260927_逐字稿_Golden_REVIEW_V7.txt"
    qa=out/"20260927_Golden_REVIEW_V7_QA.json"
    action=out/"20260927_Golden_REVIEW_V7_配時紀錄.json"
    if filename.exists() and filename.read_text()!=output:
        raise ValueError("Existing V7 SRT collision")
    filename.write_text(output)
    checked,e=parse_srt(filename)
    if e or any("\n" in c["text"] or "\r" in c["text"] for c in checked):
        raise ValueError("Written V7 SRT is not truly single line")
    chunks=[];para=""
    for c in checked:
        para+=c["text"]
        if para.endswith(("。","！","？")) or len(para)>=120:
            chunks.append(para);para=""
    if para:chunks.append(para)
    txt_text="\n\n".join(chunks)+"\n"
    if txt.exists() and txt.read_text()!=txt_text:
        raise ValueError("V7 transcript collision")
    txt.write_text(txt_text)
    if lex(txt_text)!=lex("".join(c["text"] for c in stage)):
        raise ValueError("TXT diverged from SRT")
    q={
       "schema_version":"golden24-v7-hard-single-line-v1",
       "status":"REVIEW" if not gate["passed"] else "CANDIDATE",
       "publish_allowed":False, # Only independent full acoustic QA can clear.
       "v6_sha256":sha256(v6path),"v2_sha256":sha256(v2path),
       "source_audio_sha256":manifest["source_sha256"],
       "v7_srt_sha256":sha256(filename),"v7_txt_sha256":sha256(txt),
       "cues_v6":len(cues),"cues_v7":len(checked),
       "original_multiline_cues":sum(len(c["text"].splitlines())>1 for c in cues),
       "additional_proven_time_splits":len(splits),
       "unresolved_multiline_as_timing":len(blocked),
       "resolved_with_v2_timing":sum(any(p["source"]=="original-V2-cue-start" for p in x["boundary_proof"]) for x in splits),
       "resolved_with_dual_chirp":sum(any(p["source"].startswith("Chirp-3-") for p in x["boundary_proof"]) for x in splits),
       "split_audit":splits,
       "unresolved_audio_alignment":blocked,
       "strict_gate":gate,
       "no_multiline_output":True,
       "preserved_all_lexical_chars":True,
       "v7_srt_and_txt_identical_lexical_content":True,
       "independent_full_audio_vad_and_terms_complete":False,
       "formal_drive_originals_overwritten":False,
    }
    qa.write_text(json.dumps(q,ensure_ascii=False,indent=2)+"\n")
    action.write_text(json.dumps({"splits":splits,"blocked":blocked},ensure_ascii=False,indent=2)+"\n")
    return {"status":q["status"],"publish_allowed":q["publish_allowed"],
            "cues_v6":len(cues),"cues_v7":len(checked),"multiline_output":gate["multiline_cues"],
            "additional_proven_splits":len(splits),
            "resolved_with_v2_timing":q["resolved_with_v2_timing"],
            "resolved_with_dual_chirp":q["resolved_with_dual_chirp"],
            "unresolved":len(blocked),"overlong_single_line_cues":gate["overlong_cues"],
            "hard_gate_passed":gate["passed"]}
if __name__=="__main__":
    ap=argparse.ArgumentParser()
    for k in ("v6","v2","job","out"):ap.add_argument("--"+k,type=Path,required=True)
    x=ap.parse_args()
    print(json.dumps(run(x.v6,x.v2,x.job,x.out),ensure_ascii=False,indent=2))
