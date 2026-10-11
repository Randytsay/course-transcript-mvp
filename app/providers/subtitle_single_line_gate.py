"""Hard-fail Golden Subtitle release check: a cue is exactly one timed line.

Do not use a two-line cue as a fallback for unaligned speech, and do not allow
a single overlong line to silently pass a mobile-readable subtitle QA check.
This is independent of any chosen ASR model or provisional review layout.
"""
from __future__ import annotations

SINGLE_LINE_HARD_TARGET=16

def strict_srt_gate(cues:list[dict], *,
                    max_visible_chars:int=SINGLE_LINE_HARD_TARGET,
                    require_unresolved_zero:bool=False,
                    unresolved:list[dict]|None=None,
                    strict_speech_coverage_verified:bool=False):
    problems=[]
    if not cues:problems.append({"reason":"empty-cues"})
    for i,c in enumerate(cues,1):
        t=c["text"]
        if "\n" in t or "\r" in t:
            problems.append({"id":i,"reason":"multiline-cue-prohibited"})
        if len(t)>max_visible_chars:
            problems.append({"id":i,"reason":"too-many-visible-characters",
                             "chars":len(t),"max":max_visible_chars})
        if not t.strip():problems.append({"id":i,"reason":"blank-cue"})
        if c["start_ms"]>=c["end_ms"]:
            problems.append({"id":i,"reason":"invalid-cue-timing"})
        if i>1 and cues[i-2]["end_ms"]>c["start_ms"]:
            problems.append({"id":i,"reason":"overlapping-cues"})
    if unresolved and require_unresolved_zero:
        problems.append({"reason":"unresolved-unverified-audio-timing",
                         "remaining":len(unresolved)})
    if not strict_speech_coverage_verified:
        problems.append({"reason":"independent-full-audio-acoustic-coverage-not-passed"})
    return {"passed":not problems,
            "hard_gate_blocker_count":len(problems),
            "hard_gate_blockers":problems,
            "multiline_cues":sum(("\n" in c["text"] or "\r" in c["text"]) for c in cues),
            "overlong_cues":sum(len(c["text"])>max_visible_chars for c in cues),
            "hard_line_char_limit":max_visible_chars,
            "cue_count":len(cues)}
