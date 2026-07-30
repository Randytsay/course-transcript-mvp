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

`voice_11386603-seg1` is the first formal long-file validation. Chunk 000
passed with 4,473 words; chunks 001–003 are running. Do not upload to Drive
until QA and explicit user approval.
