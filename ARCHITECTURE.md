# Architecture

## Runtime boundary

- Oracle VPS: Docker, FFmpeg, persistent job data and orchestration.
- Google Drive: source and only user-approved final-output destination.
- GCS: private input chunks and raw Chirp result JSON.
- Chirp 3 in `us`: word timestamps and subtitle timing.
- Google Vertex AI Gemini 3.6 Flash: text-only correction, never subtitle timing.

## Long-file contract

Chirp 3 word timestamps limit each BatchRecognize source file to 20 minutes.
The production target uses 15-minute chunks with 10-second overlap. A chunk is
successful only after its GCS JSON, results, alternatives, words, monotonic
timestamps, and manifest are present.

## Current job

`voice_11386603-seg1` is the first formal long-file validation (55:49.345).
It completed Chirp 3 base chunks 000–004 plus five targeted Chirp patch chunks
005–009. The authoritative merge contains 16,898 valid words and 862 fixed
subtitle segments. The final tail is 105 ms short of FFprobe duration, within
the local output QA threshold; it is not a transcription gap.

Chirp patch chunks replace words only inside their exact source windows. Base
chunks own all other words by midpoint ownership. This preserves raw GCS JSON,
allows repair of real speech gaps without re-sending the whole recording, and
prevents text-similarity merging from deleting words.

Gemini correction is complete using `gemini-3.6-flash` only: 122 raw
per-window responses, one global terminology record, and 458 corrected
segments. IDs, ordering, start times, and end times remain immutable. Do not
upload to Drive until QA and explicit user approval.
