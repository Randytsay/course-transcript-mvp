#!/usr/bin/env python3
"""Golden24 V6: word-boundary QA and evidence-backed repair of V5 seams.

This is a REVIEW-only candidate. It never invents word times, writes to the
formal Drive folder, or asserts acoustic correctness. Improves well-known
midword cue boundaries and replaces arbitrary character-count line wrapping
with punctuation-first, token-aware layout.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
from pathlib import Path
from scripts.golden24.pilot_preflight import parse_srt, sha256
from scripts.golden24.generate_review_pair import tc
from scripts.golden24.v5_semantic_layout import lex, split_timestamp
from app.providers.boundary_integrity import scan_boundary_integrity

DEPENDENCIES=Path("/opt/course-transcript-source/data/golden24/_deps")
sys.path.insert(0,str(DEPENDENCIES))
try:
    import jieba
except ImportError:
    jieba=None
if jieba is not None:
    jieba.setLogLevel(60)
PROTECTED=(
    "好一點","生活品質","彌勒佛","釋迦牟尼佛","阿耨多羅三藐三菩提心",
    "阿耨多羅","三藐三菩提心","恒河沙","恆河沙","聲聞菩提","八十八佛",
    "四大假合","有沒有","這個","這些","比較","時候","三十四億","一樣","我們","因地","可以","個人","不知道","可稱為","像佛一樣","為人",
    "摩睺羅伽","優婆塞","優婆夷","如芥子許","佛性","神通",
)
if jieba is not None:
    for p in PROTECTED:jieba.add_word(p,freq=10000000)

# Explicit source-verified seam candidates. Each pair was confirmed to break
# in the middle of a word/phrase in V5. The original cue ID is immutable.
MOVES={
    923:"這", 931:"比", 944:"怎", 952:"有", 956:"這",
    1080:"不", 1102:"這", 1103:"彌勒", 1108:"我",
    1576:"是不", 1581:"時", 1594:"這是",
}

def flatten(t:str)->str:return t.replace("\n","").replace("\r","")

def token_boundaries(text:str)->set[int]:
    if jieba is None:
        raise RuntimeError("jieba==0.42.1 must be installed in the review-only dependency target; do not force arbitrary character wraps")
    # Jieba is an editorial heuristic. It NEVER supplies a new timestamp;
    # it only prevents breaking common word and Buddhist-term sequences.
    cursor=0; pos=set()
    for word in jieba.cut(text,HMM=False):
        if not word:continue
        cursor+=len(word)
        pos.add(cursor)
    if cursor!=len(text):raise ValueError("word tokenizer reordered content")
    for term in PROTECTED:
        for m in re.finditer(re.escape(term),text):
            for p in range(m.start()+1,m.end()):
                pos.discard(p)
    return pos

def format_word_safe(text:str,preferred=15,absolute=20):
    """Layout after time segmentation. Never arbitrarily cut a Han character."""
    text=flatten(text)
    if len(text)<=preferred:return text,False
    breaks=token_boundaries(text)
    if not breaks:
        return text,True
    pos=0;out=[];needs_review=False
    while len(text)-pos>preferred:
        possible=[i for i in breaks if pos+5<=i<=min(pos+absolute,len(text)-4)]
        if not possible:
            # Honor word integrity, even if one line is overlong. QA will flag it.
            end=min([i for i in breaks if i>pos], default=len(text))
            if end<=pos:raise ValueError("zero-length line")
            out.append(text[pos:end]);pos=end
            needs_review=True
            continue
        total=len(text)-pos
        # Balance when two remaining lines can fit; otherwise prefer ~15 chars.
        target=pos+min(preferred,max(8,math.ceil(total/math.ceil(total/preferred))))
        def score(i):
            prev=text[i-1]
            punctuation=prev in "。？！；，、："
            next_starts_punctuation=i<len(text) and text[i] in "。？！；，、：」』"
            left_len=i-pos
            # End-of-line grammatical glue is usually an invalid clause break.
            # Do not leave 「一／樣」「我／們」「比較好／一點」 split.
            glue_pen=42 if prev in "的地得為把被在向將而再還一不我你他她它有可" else 0
            before_glue=28 if i<len(text) and text[i] in "的地得們樣時候" else 0
            return (0 if punctuation else 13) + abs(i-target)*2 + (100 if next_starts_punctuation else 0) + (8 if left_len<8 else 0) + glue_pen + before_glue
        end=min(possible,key=score)
        out.append(text[pos:end]);pos=end
    if pos<len(text):out.append(text[pos:])
    if "".join(out)!=text:raise ValueError("layout changed text")
    for i in range(len(out)-1):
        p=sum(map(len,out[:i+1]))
        if p not in breaks:raise ValueError("unsafe word boundary")
    return "\n".join(out),needs_review or any(len(x)>absolute for x in out)

def nearest_source_word_start(words:list[dict],suffix:str,target:int,lo:int,hi:int):
    seq=[]
    for x in words:
        if lo<=x["start_ms"]<=hi:
            t=lex(str(x["word"]))
            for ch in t:seq.append((ch,x["start_ms"]))
    string="".join(ch for ch,_ in seq)
    candidates=[]
    from_at=0
    while True:
        i=string.find(lex(suffix),from_at)
        if i<0:break
        boundary=seq[i][1]
        candidates.append(boundary)
        from_at=i+1
    candidates=[t for t in candidates if lo<=t<=hi]
    if not candidates:return None
    best=min(candidates,key=lambda t:abs(t-target))
    if abs(best-target)>1800:return None
    return best

def repair_seams(original:list[dict],words:list[dict]):
    cues=[dict(c, source_id=c["id"],text=flatten(c["text"])) for c in original]
    actions=[];failed=[]
    byid=lambda n:next((i for i,c in enumerate(cues) if c["source_id"]==n),None)
    for source_id,suffix in MOVES.items():
        i=byid(source_id)
        if i is None or i+1>=len(cues):
            failed.append({"id":source_id,"reason":"cue absent"});continue
        a,b=cues[i],cues[i+1]
        if b["source_id"]!=source_id+1 or not a["text"].endswith(suffix):
            failed.append({"id":source_id,"reason":"expected neighboring text changed"});continue
        when=nearest_source_word_start(words,suffix,a["end_ms"],
                                       max(a["start_ms"],a["end_ms"]-2500),b["end_ms"])
        if when is None or when<=a["start_ms"]+250 or when>=b["end_ms"]-250:
            failed.append({"id":source_id,"reason":"word start not source-verified"});continue
        a["text"]=a["text"][:-len(suffix)]
        if not a["text"]:
            failed.append({"id":source_id,"reason":"empty previous after movement"});continue
        b["text"]=suffix+b["text"]
        a["end_ms"]=when
        b["start_ms"]=when
        actions.append({"kind":"move whole lexeme onset","prior_id":source_id,
                        "following_id":source_id+1,"moved_text":suffix,
                        "new_word_boundary_ms":when,"evidence":"Chirp 3 lexical word start"})
    # Explicit multi-cue regrouping at the semantic phrase boundary. This is
    # more correct than merely carrying 好 into a later, unrelated sentence.
    i=byid(957)
    if i is None or i+1>=len(cues):raise ValueError("known source 957 absent")
    a,b=cues[i],cues[i+1]
    tail="這個生活品質比較好"
    if b["source_id"]!=958 or not a["text"].endswith(tail) or not b["text"].startswith("一點。"):
        raise ValueError("known midword '好|一點' seam changed unexpectedly")
    def exact_word(t,word):
        m=[x for x in words if x["start_ms"]==t and lex(x["word"])==word]
        if len(m)!=1:raise ValueError(f"Chirp provenance for {word} at {t} not exact")
    for t,w in [(3205840,"這"),(3207160,"好"),(3207360,"一"),(3207480,"點"),(3208240,"有")]:
        exact_word(t,w)
    prefix=a["text"][:-len(tail)]
    remainder=b["text"][len("一點。"):]
    if not prefix.endswith("，") or not remainder.startswith("有精神"):
        raise ValueError("semantic phrase anchors mismatch")
    new_a=dict(a,text=prefix,end_ms=3205840)
    mid={"start_ms":3205840,"end_ms":3208240,
         "text":tail+"一點。","source_id":"957:sentence2"}
    new_b=dict(b,text=remainder,start_ms=3208240)
    cues[i:i+2]=[new_a,mid,new_b]
    actions.append({"kind":"regroup phrase across two source cues",
                    "ids":[957,958],"phrases":[prefix,mid["text"],new_b["text"]],
                    "boundaries_ms":[3205840,3208240],
                    "evidence":"verified full Chirp 3 characters and word onsets"})
    # Repair earlier parts of the very same utterance. These four start times
    # are historical Chirp 3 word onsets, not arithmetic interpolation.
    k=byid(956)
    a0,a1=cues[k:k+2]
    phrase1="所以說，四大，"
    phrase2="我們要維持這個生命，"
    phrase3="為這個維持這個生命有一個目的，"
    phrase4="就是我們要生活，"
    if a0["text"]+a1["text"]!=phrase1+phrase2+phrase3+phrase4:
        raise ValueError("00:53:20 semantic text sequence not as expected")
    for ts,wd in [(3199360,"我"),(3200840,"為"),(3204000,"就"),(3205840,"這")]:
        exact_word(ts,wd)
    bounds=[a0["start_ms"],3199360,3200840,3204000,3205840]
    rebuilt=[]
    for j,text in enumerate([phrase1,phrase2,phrase3,phrase4]):
        rebuilt.append({"start_ms":bounds[j],"end_ms":bounds[j+1],
                        "source_id":f"956:sub{j+1}","text":text})
    cues[k:k+2]=rebuilt
    actions.append({"kind":"regroup coherent spoken clauses",
                    "ids":[956,957],"phrases":[phrase1,phrase2,phrase3,phrase4],
                    "boundaries_ms":bounds[1:-1],
                    "evidence":"source-verified Chirp 3 individual word starts"})
    if any(c["start_ms"]>=c["end_ms"] for c in cues):raise ValueError("duration invalid")
    if any(x["end_ms"]>y["start_ms"] for x,y in zip(cues,cues[1:])):
        raise ValueError("seam repair introduced overlaps")
    return cues,actions,failed

def lex_words(cues):return lex("".join(c["text"] for c in cues))

def evaluate_seams(cues:list[dict]):
    entries=[]
    for i,c in enumerate(cues):
        for j,line in enumerate(c["text"].splitlines()):
            entries.append({"segment_id":str(c.get("source_id",c.get("id")))+":"+str(j),
                            "start_ms":c["start_ms"],"end_ms":c["end_ms"],"text":line})
    r=scan_boundary_integrity(entries)
    # Diligence: separate within-caption word-break checks and adjacent cue checks.
    suspected=[]
    for row in r["split_candidates"]:
        term=row["term"]
        suspected.append({"before":row["before_segment_id"],
                          "after":row["after_segment_id"],"term":term,
                          "intact_occurrences":row["intact_elsewhere_count"]})
    # Corpus scanner is probabilistic: flagged items require review.
    return suspected

def render(cues):
    return "\n".join(f"{i}\n{tc(x['start_ms'])} --> {tc(x['end_ms'])}\n{x['text']}\n"
                      for i,x in enumerate(cues,1))+"\n"

def make(v5:Path,job:Path,v2:Path,dest:Path):
    srt=v5/"20260927_Golden_REVIEW_V5_語意斷句.srt"
    txt=v5/"20260927_逐字稿_Golden_REVIEW_V5.txt"
    qa=json.loads((v5/"20260927_Golden_REVIEW_V5_QA.json").read_text())
    if qa.get("publish_allowed") is not False or qa.get("v5_srt_sha256")!=sha256(srt):
        raise ValueError("Not verified V5 REVIEW")
    old,err=parse_srt(srt)
    if err:raise ValueError(err[:5])
    ref=json.loads((job/"source-manifest.json").read_text())
    if ref.get("source_sha256")!=qa.get("source_chirp_sha256"):
        raise ValueError("Audio hash binding mismatch")
    words=json.loads((job/"merged-words.json").read_text())["words"]
    old_v2_path=v2/"20260927_Golden_REVIEW.srt"
    v2_qa=json.loads((v2/"20260927_Golden_REVIEW_QA.json").read_text())
    if v2_qa["candidate_srt_sha256"]!=sha256(old_v2_path):
        raise ValueError("V2 original cue boundary evidence SHA mismatch")
    v2cues,e2=parse_srt(old_v2_path)
    if e2 or lex_words(v2cues)!=lex_words(old):
        raise ValueError("V2 lexical provenance diverged")
    v2_boundaries={}
    offset=0
    for item in v2cues:
        v2_boundaries[offset]=item["start_ms"]
        offset+=len(lex(flatten(item["text"])))
    cues,changes,failed=repair_seams(old,words)
    if lex_words(cues)!=lex_words(old):raise ValueError("Word content changed by seam repair")
    layout_exceptions=[]
    for c in cues:
        t=c["text"]
        c["text"],flag=format_word_safe(t)
        if flag or len(c["text"].splitlines())>2:
            layout_exceptions.append({"source_id":c["source_id"],
                                      "start_ms":c["start_ms"],
                                      "lines":c["text"].splitlines(),
                                      "reason":"needs speech-aligned segmentation; did not force character split"})
    if lex_words(cues)!=lex_words(old):
        raise ValueError("word layout changed contents")
    # A display linebreak is not a separate subtitle timing segment.
    # Upgrade it to a REAL timed cue only when Chirp 3 word anchors agree.
    timed_parts=[]
    sound_splits=[]
    layout_only=[]
    global_offset=0
    for source in cues:
        lines=source["text"].splitlines()
        phrase_start_offset=global_offset
        global_offset+=len(lex(flatten(source["text"])))
        if len(lines)<2:
            timed_parts.append(source);continue
        exact=[]
        char_cursor=phrase_start_offset
        for l in lines[:-1]:
            char_cursor+=len(lex(l))
            when=v2_boundaries.get(char_cursor)
            if when is None:
                exact=[];break
            exact.append(when)
        if exact and all(b-a>=550 for a,b in zip(
            [source["start_ms"]]+exact,exact+[source["end_ms"]])):
            evidence,ratio=exact,1.0
            source_provenance="immutable V2 cue boundary"
        else:
            evidence,ratio=split_timestamp(flatten(source["text"]),lines,words,
                                           source["start_ms"],source["end_ms"])
            source_provenance="Chirp 3 word-level alignment"
        if evidence is None or ratio<0.85:
            timed_parts.append(source)
            layout_only.append({"source_id":source["source_id"],"start_ms":source["start_ms"],
                                "cue_lines":lines,"similarity":round(ratio,3),
                                "reason":"word-level acoustic timing not reliable; visual wrap only"})
            continue
        starts=[source["start_ms"]]+evidence
        ends=evidence+[source["end_ms"]]
        for i,(body,lo,hi) in enumerate(zip(lines,starts,ends)):
            if not lo<hi:raise ValueError("audio timed segment invalid")
            timed_parts.append({**source,"source_id":str(source["source_id"])+":"+str(i),
                                "start_ms":lo,"end_ms":hi,"text":body})
        sound_splits.append({"source_id":source["source_id"],"word_match_ratio":round(ratio,3),
                             "source_provenance":source_provenance,
                             "boundaries_ms":evidence,"speech_segment_count":len(lines)})
    cues=timed_parts
    if lex_words(cues)!=lex_words(old):raise ValueError("time splits changed words")
    if any(y["start_ms"]<x["end_ms"] for x,y in zip(cues,cues[1:])):
        raise ValueError("SRT display overlaps")
    dest.mkdir(parents=True,exist_ok=True)
    sp=dest/"20260927_Golden_REVIEW_V6_詞組時間校正.srt"
    tp=dest/"20260927_逐字稿_Golden_REVIEW_V6.txt"
    qp=dest/"20260927_Golden_REVIEW_V6_QA.json"
    diff=dest/"20260927_Golden_REVIEW_V6_改動明細.json"
    s=render(cues)
    if sp.exists() and sp.read_text()!=s:raise ValueError("Cannot overwrite different V6 output")
    sp.write_text(s,encoding="utf8")
    parsed,errors=parse_srt(sp)
    if errors:raise ValueError(errors[:5])
    chunks=[];paragraph=""
    for x in parsed:
        paragraph+=flatten(x["text"])
        if paragraph.endswith(("。","？","！")) or len(paragraph)>=140:
            chunks.append(paragraph);paragraph=""
    if paragraph:chunks.append(paragraph)
    transcript="\n\n".join(chunks)+"\n"
    if tp.exists() and tp.read_text()!=transcript:raise ValueError("V6 transcript collision")
    tp.write_text(transcript,encoding="utf8")
    if lex(transcript)!=lex_words(old):raise ValueError("Transcript mismatches original words")
    suspicious=evaluate_seams(parsed)
    def bound_term(left,right):return flatten(left["text"])[-6:]+"/"+flatten(right["text"])[:6]
    bad_examples=[]
    for x,y in zip(parsed,parsed[1:]):
        combined=flatten(x["text"])[-12:]+flatten(y["text"])[:12]
        sep=len(flatten(x["text"])[-12:])
        for term in PROTECTED:
            i=combined.find(term)
            if i>=0 and i<sep<i+len(term):
                bad_examples.append({"prev_cue":x["id"],"next_cue":y["id"],"term":term,"at_ms":y["start_ms"]})
    layout_bad=[]
    for c in parsed:
        for i,line in enumerate(c["text"].splitlines()[:-1]):
            full=flatten(c["text"])
            idx=sum(len(l) for l in c["text"].splitlines()[:i+1])
            for term in PROTECTED:
                for m in re.finditer(re.escape(term),full):
                    if m.start()<idx<m.end():
                        layout_bad.append({"cue":c["id"],"term":term})
    # Do not claim QA PASS when the original acoustic words are inconsistent.
    # Internal word-splits are preventable: fail; cross-cue ambiguities are
    # explicitly listed and block the Golden release.
    if layout_bad:
        raise ValueError(f"Unsafe V6 internal wrap: {layout_bad[:5]}")
    report={
        "schema_version":"golden24-v6-word-safe-review-v1",
        "status":"REVIEW","publish_allowed":False,
        "source_chirp_sha256":ref["source_sha256"],
        "source_v5_sha256":sha256(srt),
        "candidate_srt_sha256":sha256(sp),
        "candidate_txt_sha256":sha256(tp),
        "text_identical_ignoring_punctuation_and_linebreaks":True,
        "source_cues":len(old),"review_cues":len(parsed),
        "source_verified_cross_cue_repairs":len(changes),
        "source_verified_cross_cue_rejected":failed,
        "layout_word_or_line_exceptions":layout_exceptions,
        "semantic_line_structure_with_no_forced_character_breaks":True,
        "resegmented_with_evidence_based_times":len(sound_splits),
        "resegmented_timing_details":sound_splits,
        "layout_only_timing_unverified":layout_only,
        "layout_only_timing_unverified_count":len(layout_only),
        "lines_over_16":sum(len(line)>16 for c in parsed for line in c["text"].splitlines()),
        "lines_over_20":sum(len(line)>20 for c in parsed for line in c["text"].splitlines()),
        "three_or_more_line_cues":sum(len(c["text"].splitlines())>=3 for c in parsed),
        "known_cross_line_protected_terms_broken":len(layout_bad),
        "known_cross_cue_protected_terms_broken":len(bad_examples),
        "known_cross_cue_issues":bad_examples,
        "heuristic_intact_term_split_candidates":suspicious,
        "remaining_release_blockers":[
            "some cues still require independent voice/word acoustic alignment",
            "cross-cue audible conflict at ~01:28:15 must be checked against audio",
            "full audio coverage and Buddhist lexicon proofreading incomplete",
            "per-device subtitle render timing not measured",
        ],
        "unverified_actual_acoustic_semantics":True,
        "formal_drive_modified":False,
    }
    qp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    diff.write_text(json.dumps(changes,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    return report

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for v in ("v5","v2","job","out"):p.add_argument("--"+v,type=Path,required=True)
    args=p.parse_args()
    q=make(args.v5,args.job,args.v2,args.out)
    print(json.dumps({k:v for k,v in q.items() if k not in ("layout_word_or_line_exceptions","heuristic_intact_term_split_candidates")},ensure_ascii=False,indent=2))
