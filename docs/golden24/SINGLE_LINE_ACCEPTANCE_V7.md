# Golden Transcript — mandatory one-line acoustic subtitle release gate

A Golden Subtitle is one semantic, readable phrase assigned its own audible
word-time interval. This is not a two-line "visual wrap" system.

- one SRT cue has exactly one non-empty text line and <=16 displayed characters
- no lines may split words/protected Buddhist terms
- every new boundary has traceable original-Chirp-word or accepted V2 timing
- no timed word omission, no invented/duplicated transcript text
- no timestamp overlap; no full-file shift to conceal late display
- SRT and TXT must preserve the exact same lexical words
- independent full-audio speech/VAD coverage is required
- if any criterion fails, status remains REVIEW and publish_allowed=false

Implementation enforcement is in app/providers/validate_outputs.py and
app/providers/subtitle_single_line_gate.py, not just the V7 generator.
All later sessions should run these before a formal publish or Drive overwrite.

For the 2026-09-27 pilot, see HANDOFF_20261011.md, the V7 QA and
20260927_Golden_REVIEW_V7_待修聲學清單.json in review_v7_final.
Unverified breaks require short bounded voice reruns, one clip at a time.
Do not overwrite the existing Drive source, nor mass-approve exceptions.
