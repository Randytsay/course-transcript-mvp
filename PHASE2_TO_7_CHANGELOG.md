# Course Transcript MVP — Phase 2–7 change record

Date: 2026-07-30 (Asia/Taipei)

This record covers the approved five-minute sample only. No Drive source file,
bucket, IAM role, firewall rule, or existing VPS service was modified.

## Added or changed source files

- `app/phase3_chirp_timeline.py` — Chirp 3 word-timestamp retrieval. Updated
  to use a temporary GCS batch-result object rather than an inline response.
- `app/phase4_gemini_chunks.py` — diagnostic overlapping Gemini 3.6 Flash
  chunk pass; retained as evidence that long model output can stop early.
- `app/phase5_fill_gaps.py` — targeted diagnostic gap pass; retained as
  evidence that Gemini audio timestamps are not a safe subtitle source.
- `app/phase6_gemini_microchunks.py` — approved Gemini 3.6 Flash, ten
  deterministic 30-second transcription inputs.
- `app/phase7_align_srt.py` — deterministic text-to-Chirp-word-timeline
  alignment and candidate SRT emitter.

## Result files produced

- `phase3-chirp3-words.json` — Chirp timing reference, 1,210 words.
- `phase6-gemini-3.6-flash-microchunks.{json,txt}` — Gemini 3.6 Flash main
  transcript evidence.
- `phase7-gemini-3.6-flash-aligned.{json,srt}` — review-candidate alignment
  and SRT.
- `phase7-alignment-qa.json` — alignment quality evidence.

## Temporary-object policy

Every cloud object created for this work used `test/phase3/`, `test/phase4/`,
`test/phase5/`, or `test/phase6/`. Each prefix was checked empty after its
run. Service-account credentials were mounted read-only and were never read,
copied, printed, or added to this project.
