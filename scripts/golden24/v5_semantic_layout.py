#!/usr/bin/env python3
"""Evidence-aligned punctuation / semantic caption layout candidate for 20260927.

* Never overwrites V4 or Google Drive.
* Adds only editorial punctuation to known hard ASR fallback passages.
* Splits only at proven historic Chirp-3 word start times with robust text matching.
* Keeps exactly the original sequence of lexical characters; TXT reflow from new SRT.
* Leaves uncertain timings/corrections in the REVIEW exception manifest.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import re
from bisect import bisect_left
from pathlib import Path
from scripts.golden24.pilot_preflight import parse_srt, sha256
from scripts.golden24.generate_review_pair import tc

# Exact-substring punctuation only. The original lexical text is never changed,
# even when this transcript contains suspected transcription errors.
# This is not yet an acoustic validation of words or Buddhist terminology.
FALLBACK_PUNCT={
901:[("再來接下去", "再來，接下去"),("為病人而入城腦病人啊病人那就是","為病人而入城。腦病人啊，病人那就是")],
902:[("四大假合四大假合生病了生病的時候","四大假合。四大假合生病了，生病的時候"),("需要有人看護啊","需要有人看護啊，")],
903:[("個這些都是比較比較悲苦的貧苦的人生病比較痛苦","個，這些都是比較比較悲苦的，貧苦的人生病比較痛苦")],
904:[("貴人生病哦一樣是病啊","貴人生病哦，一樣是病啊。")], # corrected variant rejected by lexical gate
905:[("有沒有一樣啊不一樣對不對對嘛","有沒有一樣啊？不一樣。對不對？對嘛，")],
906:[("錢有錢可依啊這個其他這個生那其他方面可以補足","錢，有錢可依啊。這個，其他，這個生，那其他方面可以補足")],
907:[("較沒有那麼痛苦貧苦者哇苦苦惱無量啊苦惱無量","較沒有那麼痛苦。貧苦者，哇，苦苦惱無量啊，苦惱無量。")],
908:[("好所以說有時候有時候這些這些還有還有這些比丘這個","好，所以說，有時候，有時候這些，這些還有，還有這些比丘，這個")],
909:[("比丘比丘大眾會會生病請教的時候病生病那都必須要有","比丘，比丘大眾會會生病，請教的時候病，生病那都必須要有")],
910:[("有必須要有看護所以說如來會為為他們安慰或講因果有","有，必須要有看護。所以說，如來會為為他們安慰或講因果，有")],
911:[("時候病是四大但是真是真實的是業障哦有定","時候病是四大，但是，真是真實的是業障哦，有定")],
912:[("業現前好把真假分清楚好那我們自性是真","業現前。好，把真假分清楚。好，那我們自性是真")],
913:[("哦那四大是假","哦，那四大是假。")],
914:[("好我們為要維持生命我們要這個四大要維持生命怎麼怎","好，我們為要維持生命，我們要這個四大要維持生命，怎麼怎")],
915:[("麼要不要吃對怎麼那麼我們一天不吃可不可以","麼，要不要吃？對。怎麼，那麼我們一天不吃可不可以？")],
916:[("可以啦啊是不是像生病一樣","可以啦。啊，是不是像生病一樣？")],
917:[("鬼哭那個個頭暈啊好兩天不吃飯哇糟糕了躺在那","鬼哭那個個頭暈啊。好，兩天不吃飯，哇，糟糕了，躺在那")],
918:[("裡是不是像生病一樣對啊吃一點飯時體力又起來了有","裡，是不是像生病一樣？對啊，吃一點飯時體力又起來了，有")],
919:[("沒有對以食為藥","沒有？對，以食為藥。")],
920:[("所以說我們在吃飯的時候就是在吃藥","所以說，我們在吃飯的時候就是在吃藥。")],
921:[("以食為藥所以說四大我們要維持這個生命為這個維持這","以食為藥。所以說，四大，我們要維持這個生命，為這個維持這")],
922:[("個生命有一個目的就是我們要生活這個生活品質比較好","個生命有一個目的，就是我們要生活，這個生活品質比較好")],
923:[("一點有精神我有體力哦明天可以工作還有我們可以集會","一點。有精神，我有體力哦，明天可以工作，還有我們可以集會。")],
924:[("我們這個精神很好有沒有這樣對就是吃飯吃飯就是目的","我們這個精神很好，有沒有這樣？對，就是吃飯，吃飯就是目的，")],
925:[("要讓這個身體維持壽命啊還有這個生活品質比較好一點","要讓這個身體維持壽命啊，還有這個生活品質比較好一點。")],
1031:[("如來入城為何為誰入城為80億三會龍華之後","如來入城為何？為誰入城？為80億。三會龍華之後，")],
1032:[("那有很多有善根者","那有很多有善根者。")],
1033:[("有善根的修行很認真的非常認真比較認真一生","有善根的修行，很認真的，非常認真，比較認真，一生")],
1034:[("修行一生專注","修行，一生專注。")],
1035:[("啊但是是這個這個腦子腦子啊沒有轉變誒不","啊，但是，是這個，這個腦子，腦子啊沒有轉變。誒，不")],
1036:[("要說不要說因緣不好啊","要說，不要說因緣不好啊。")],
1037:[("啊真的是被這個被這個世間人情所綁所綁了啊","啊，真的是被這個，被這個世間人情所綁，所綁了啊。")],
1038:[("世間人有很多修行人修的非常不可思議非常好非常專注","世間人有很多修行人，修的非常不可思議，非常好，非常專注。")], # lexical modified, auto gate reject
1039:[("但是腦子就是被人情綁住","但是腦子就是被人情綁住。")],
1040:[("我們都講佛經","我們都講佛經。")],
1041:[("但是世間所有一一切道場都講什麼","但是世間所有一一切道場都講什麼？")],
1042:[("祖師祖師嘛東邊供祖師東邊供祖師","祖師，祖師嘛！東邊供祖師，東邊供祖師。")],
1043:[("啊跟他講都轉不過來","啊，跟他講都轉不過來。")],
1044:[("怎麼轉就轉不過來他通常要講祖師他講佛都不行那裡啊","怎麼轉就轉不過來。他通常要講祖師，他講佛都不行，那裡啊。")],
1045:[("所以說所以說必須要請祖師下來加祖師所以說才有祖師","所以說，所以說必須要請祖師下來，加祖師，所以說才有祖師。")],
1046:[("啊哎十方淨土哪有祖師祖師在後面在在在在在一個80","啊，哎，十方淨土哪有祖師？祖師在後面，在在在在，在一個80")],
1047:[("億的沒啊呢嘛啊是不是佛彌釋迦牟尼佛就就知道了照的","億的沒啊呢嘛啊。是不是佛彌釋迦牟尼佛就就知道了照的")],
1048:[("怎樣5只怎樣幾啊你幾嗎嗎","怎樣5只怎樣幾啊？你幾嗎嗎？")],
1049:[("幾嗎怎樣幾呀早就定下來了哎早就定下來了此經","幾嗎怎樣幾呀。早就定下來了，哎，早就定下來了。此經")],
1050:[("的經名有七種","的經名有七種。")],
1051:[("有還有另外一另外一種就是釋迦牟尼佛以依為證這","有，還有另外一，另外一種就是釋迦牟尼佛以依為證，這")],
1052:[("些人必須要釋迦牟尼佛的僧侶衣來證明他是彌勒","些人必須要釋迦牟尼佛的僧侶衣來證明他是彌勒")],
1053:[("佛他才相信彌勒佛下生","佛，他才相信彌勒佛下生。")],
1054:[("啊還要有誰來還要祖師來","啊，還要有誰來？還要祖師來。")],
1055:[("啊所以說我們翻開第80頁快要到了第80頁","啊，所以說我們翻開第80頁，快要到了，第80頁。")],
1056:[("等一下算賬等一下講經算賬哦算的這個是講事啊講事我","等一下算賬，等一下講經算賬哦，算的這個是講事啊，講事我")],
1057:[("們我們信的比較入而且很甘願的龍華三會龍華出會要來","們，我們信的比較入，而且很甘願的。龍華三會，龍華出會要來")],
1058:[("很甘願哎而且來的時候會非常歡喜哦嘎仔扎姐啊","很甘願，哎，而且來的時候會非常歡喜哦。嘎仔扎姐啊")],
1059:[("今嘛滿景上呃最後最後一去","今嘛滿景上，呃，最後，最後一去。")],
}

PUNCT="，。！？；：、,.!?;:「」『』（）() \n"
def lex(s:str)->str:
    return "".join(c for c in s if c not in PUNCT).replace("\t","")

def curated(cues:list[dict]):
    issues=[]
    count=0
    out=[]
    for c in cues:
        d=dict(c)
        d["text"]=d["text"].replace("\n","")
        patches=FALLBACK_PUNCT.get(c["id"],[])
        for before,after in patches:
            if before not in d["text"]:
                issues.append({"id":c["id"],"reason":"missing phrase, not patched",
                               "text":before})
                continue
            if lex(before)!=lex(after):
                issues.append({"id":c["id"],"reason":"replacement changes lexical content, skipped",
                               "from":before,"to":after})
                continue
            d["text"]=d["text"].replace(before,after,1)
            count+=1
        if lex(d["text"])!=lex(c["text"]):
            raise ValueError(f"Punctuation edit changed words in cue {c['id']}")
        out.append(d)
    return out,issues,count

def render_lines(text:str,max_line:int=14)->str:
    """Wrap at punctuation first, then safe character boundaries. Never alter text."""
    plain=text.replace("\n","").strip()
    if len(plain)<=max_line:return plain
    n=len(plain)
    # Dynamic programming: minimize line count & punctuation-awkward wraps.
    dp=[(float("inf"),None)]*(n+1)
    dp[n]=(0,None)
    for i in range(n-1,-1,-1):
        best=(float("inf"),None)
        for j in range(i+1,min(i+max_line,n)+1):
            if plain[j-1] in "「『（":continue
            if j<n and plain[j] in "，。？！；：、」』）":continue
            leftover=max_line-(j-i)
            pen=(leftover*leftover)*0.005
            if j<n:
                pen+=0 if plain[j-1] in "，。？！；：、" else 4
                pen+=2 if j-i<5 else 0
            cost=20+pen+dp[j][0]
            if cost<best[0]:best=(cost,j)
        if best[1] is None:
            best=(20+dp[min(i+max_line,n)][0], min(i+max_line,n))
        dp[i]=best
    i=0;pieces=[]
    while i<n:
        j=dp[i][1]
        if j is None or j<=i:raise ValueError("broken word-wrap")
        pieces.append(plain[i:j]);i=j
    if "".join(pieces)!=plain:raise ValueError("Wrap lost text")
    return "\n".join(pieces)

def canonical(s:str)->str:
    # Audio + scripture use both ASCII and Chinese punctuation.
    return re.sub(r"[^\w\u3400-\u9fff]","",s).casefold()

def split_timestamp(text:str,parts:list[str],w:list[dict],st:int,en:int):
    """Timestamp one split at a matching Chirp word boundary. Fail closed."""
    tokens=[x for x in w if st-140<=x["start_ms"]<en+140]
    chars=[]; times=[]
    for tok in tokens:
        raw=canonical(tok["word"])
        chars.extend(list(raw));times.extend([tok["start_ms"]]*len(raw))
    dst=canonical(text)
    src="".join(chars)
    if not dst or not src:return None,0.0
    matcher=difflib.SequenceMatcher(None,dst,src,autojunk=False)
    ratio=matcher.ratio()
    if ratio<0.69:return None,ratio
    splits=[]
    pos=0
    for part in parts[:-1]:
        pos+=len(canonical(part))
        candidates=[]
        for m in matcher.get_matching_blocks():
            if m.size==0:continue
            # Match offset at the beginning of the NEXT text after a proposed split.
            for idx in range(max(m.a,pos-2),min(m.a+m.size,pos+3)):
                offset=m.b+(idx-m.a)
                if 0<=offset<len(times):
                    candidates.append((abs(idx-pos),idx,times[offset]))
        if not candidates:return None,ratio
        candidates.sort()
        if candidates[0][0]>2:return None,ratio
        hit=candidates[0][2]
        if hit<st+550 or hit>en-550:return None,ratio
        splits.append(hit)
    if any(b<=a+500 for a,b in zip([st]+splits,splits+[en])):return None,ratio
    return splits,ratio

def fragments(text:str):
    """Split complete sentences first, then balanced long clauses.

    Punctuation is an editorial marker, not timestamp evidence. The caller only
    allows the split if the word boundary is found in historic Chirp 3.
    """
    text=text.replace("\n","")
    if len(text)<=28 and not any(ch in text[:-1] for ch in "。？！"):
        return [text]
    strong=[i+1 for i,c in enumerate(text[:-1]) if c in "。？！"]
    for i in strong:
        if i>=4 and len(text)-i>=4:
            return fragments(text[:i])+fragments(text[i:])
    if len(text)<=28:return [text]
    cuts=[i+1 for i,c in enumerate(text[:-1]) if c in "，；、：" and
          i+1>=6 and len(text)-(i+1)>=6]
    if not cuts:return [text]
    center=len(text)/2
    mid=min(cuts,key=lambda i:(abs(i-center),0 if text[i-1] in "，；：" else 1))
    return fragments(text[:mid])+fragments(text[mid:])


def v2_boundary_for_parts(cue_text:str, parts:list[str], prefix_lex:int,
                          boundaries:dict[int,int], start_ms:int, end_ms:int):
    """Reuse real cue starts of the V2 that the user already accepted.

    Matching is full-length lexical offset, not heuristic string searching.
    This can safely undo V3's short-cue merging across distinct sentences.
    """
    cuts=[];offset=prefix_lex
    for part in parts[:-1]:
        offset+=len(lex(part))
        when=boundaries.get(offset)
        if when is None or when<start_ms+550 or when>end_ms-550:
            return None
        cuts.append(when)
    if any(b-a<550 for a,b in zip([start_ms]+cuts,cuts+[end_ms])):
        return None
    return cuts

def split_long(cues:list[dict], v3:list[dict], words:list[dict],v2:list[dict]):
    out=[];change=[];fails=[]
    v2_boundaries={}
    pos=0
    for old in v2:
        v2_boundaries[pos]=old["start_ms"]
        pos+=len(lex(old["text"]))
    v3_prefix=0
    for i,c in enumerate(cues):
        assert c["id"]==v3[i]["id"]
        t=c["text"]
        original_prefix=v3_prefix
        v3_prefix+=len(lex(t))
        parts=fragments(t)
        if len(parts)==1:
            out.append(dict(c));continue
        starts,ends=v3[i]["start_ms"],v3[i]["end_ms"]
        boundaries=v2_boundary_for_parts(t,parts,original_prefix,
                                          v2_boundaries,starts,ends)
        ratio=1.0 if boundaries is not None else 0.0
        evidence="reused V2 source cue boundary" if boundaries is not None else "Chirp 3 exact/near lexical word anchor"
        if boundaries is None:
            boundaries,ratio=split_timestamp(t,parts,words,starts,ends)
        if boundaries is None and c["id"]==298 and len(parts)==2 and            parts[-1].startswith("生緣老死憂悲苦惱等"):
            # Verified directly from preserved source word list. Do not allow
            # this exception if source timestamps or words ever differ.
            exact=[i for i,x in enumerate(words) if x["start_ms"]==1054320 and lex(x["word"])=="生"]
            if exact and "".join(lex(words[exact[0]+k]["word"]) for k in range(3))=="生緣老":
                boundaries=[1054320];ratio=1.0
                evidence="explicitly source-verified Chirp 3 word timestamps"
        if boundaries is None:
            fails.append({"id":c["id"],"start_ms":c["start_ms"],
                          "reason":"insufficient Chirp lexical alignment",
                          "similarity":round(ratio,3),"candidate_parts":parts})
            out.append(dict(c));continue
        points=[c["start_ms"]]+boundaries+[c["end_ms"]]
        if any(x>=y for x,y in zip(points,points[1:])):
            raise ValueError("Segment timestamp overlaps")
        for j,piece in enumerate(parts):
            out.append({"start_ms":points[j],"end_ms":points[j+1],
                        "text":piece,"original_id":c["id"],"split_part":j})
        change.append({"original_id":c["id"],"source_v3_start_ms":starts,
                       "source_v3_end_ms":ends,"match_similarity":round(ratio,3),
                       "parts":parts,"new_boundaries_ms":boundaries,"evidence":evidence})
    return out,change,fails

def make(v4:Path,v3:Path,job:Path,output:Path,v2dir:Path):
    src=v4/"20260927_Golden_REVIEW_V4_提前顯示.srt"
    q=json.loads((v4/"20260927_Golden_REVIEW_V4_QA.json").read_text())
    old,issues=parse_srt(src)
    original,_=parse_srt(v3/"20260927_Golden_REVIEW_V3.srt")
    v2,_=parse_srt(v2dir/"20260927_Golden_REVIEW.srt")
    v2_metadata=json.loads((v2dir/"20260927_Golden_REVIEW_QA.json").read_text())
    if v2_metadata["candidate_srt_sha256"]!=sha256(v2dir/"20260927_Golden_REVIEW.srt"):
        raise ValueError("V2 time-boundary source was modified")
    if lex("".join(x["text"] for x in v2))!=lex("".join(x["text"] for x in original)):
        raise ValueError("V2/V3 lexical positions incompatible")
    if issues:raise ValueError(issues[:2])
    if q.get("publish_allowed") is not False or q["v4_srt_sha256"]!=sha256(src):
        raise ValueError("V4 input changed or unexpectedly releasable")
    if sha256(v3/"20260927_Golden_REVIEW_V3.srt")!=q["v3_srt_sha256"]:
        raise ValueError("V3 speech anchor changed")
    if len(old)!=len(original):raise ValueError("V4/V3 cue count mismatch")
    for a,b in zip(old,original):
        if lex(a["text"])!=lex(b["text"]):raise ValueError("V3/V4 lexical mismatch")
    source=json.loads((job/"source-manifest.json").read_text())
    if q.get("formal_drive_file_modified") is not False or source.get("source_sha256") is None:
        raise ValueError("Immutable Chirp source unavailable")
    words=json.loads((job/"merged-words.json").read_text())["words"]
    edited,typos,patches=curated(old)
    pieces,changes,fails=split_long(edited,original,words,v2)
    for c in pieces:c["text"]=render_lines(c["text"],14)
    orig_text=lex("".join(x["text"] for x in old))
    new_text=lex("".join(x["text"] for x in pieces))
    if orig_text!=new_text:raise ValueError("Lexical content diverged")
    if any(b["start_ms"]<a["end_ms"] for a,b in zip(pieces,pieces[1:])):
        raise ValueError("New cues overlap")
    if any(x["start_ms"]<0 or x["end_ms"]<=x["start_ms"] for x in pieces):
        raise ValueError("Illegal cue durations")
    output.mkdir(parents=True,exist_ok=True)
    srt=output/"20260927_Golden_REVIEW_V5_語意斷句.srt"
    txt=output/"20260927_逐字稿_Golden_REVIEW_V5.txt"
    qa=output/"20260927_Golden_REVIEW_V5_QA.json"
    changes_out=output/"20260927_Golden_REVIEW_V5_edits.json"
    rendered="\n".join(f"{i}\n{tc(x['start_ms'])} --> {tc(x['end_ms'])}\n{x['text']}\n"
                       for i,x in enumerate(pieces,1))+"\n"
    if srt.exists() and srt.read_text()!=rendered:
        raise ValueError("Refuse silently overwrite candidate")
    srt.write_text(rendered,encoding="utf8")
    parsed,errs=parse_srt(srt)
    if errs:raise ValueError(errs[:3])
    # Sentence-aware TXT presentation: preserve every cue word in the same order.
    full="".join(x["text"].replace("\n","") for x in parsed)
    paragraphs=[];buff=""
    for x in parsed:
        s=x["text"].replace("\n","")
        buff+=s
        if buff.endswith(("。","？","！")) or len(buff)>120:
            paragraphs.append(buff);buff=""
    if buff:paragraphs.append(buff)
    candidate_txt="\n\n".join(paragraphs)+"\n"
    if txt.exists() and txt.read_text()!=candidate_txt:
        raise ValueError("Refuse silently overwrite TXT")
    txt.write_text(candidate_txt,encoding="utf8")
    if lex(candidate_txt)!=new_text:raise ValueError("SRT/TXT drift")
    count_over=lambda l:sum(len(line)>l for x in parsed for line in x["text"].splitlines())
    metrics={
        "v4_total_cues":len(old),"v5_total_cues":len(parsed),
        "v4_lines_over_14":sum(len(line)>14 for x in old for line in x["text"].splitlines()),
        "v5_lines_over_14":count_over(14),
        "v4_lines_over_16":sum(len(line)>16 for x in old for line in x["text"].splitlines()),
        "v5_lines_over_16":count_over(16),
        "v5_cues_three_or_more_lines":sum(len(x["text"].splitlines())>2 for x in parsed),
        "semantic_splits_applied":len(changes),
        "punctuation_replacements_applied":patches,
        "unpunctuated_fallback_patches_rejected":len(typos),
        "timing_splits_rejected":len(fails)
    }
    qa_data={
        "schema_version":"golden24-v5-semantic-review-v1",
        "status":"REVIEW","publish_allowed":False,"date":"20260927",
        "source_chirp_sha256":source["source_sha256"],
        "v4_source_sha256":sha256(src),
        "v5_srt_sha256":sha256(srt),"v5_txt_sha256":sha256(txt),
        "all_lexical_characters_preserved_from_v4":True,
        "srt_txt_lexical_characters_identical":True,
        "no_new_timing_without_chirp_word_match":True,
        "metrics":metrics,"punctuation_patch_exceptions":typos,
        "timing_split_exceptions":fails,
        "acoustic_word_correction_complete":False,
        "independent_vad_gate_passed":False,
        "formal_drive_modified":False,
        "limitations":"Human-confirmed lexical errors still need acoustic review; punctuation proposed without pretending words are verified.",
    }
    qa.write_text(json.dumps(qa_data,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    changes_out.write_text(json.dumps({"time_splits":changes,"punctuation_patches":patches,
                                        "edit_exceptions":typos},ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    return qa_data

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for opt in ("v4","v3","v2","job","out"):p.add_argument("--"+opt,required=True,type=Path)
    a=p.parse_args()
    d=make(a.v4,a.v3,a.job,a.out,a.v2)
    print(json.dumps({k:v for k,v in d.items() if k in
                      ["status","publish_allowed","metrics","punctuation_patch_exceptions"]},
                     ensure_ascii=False,indent=2))
