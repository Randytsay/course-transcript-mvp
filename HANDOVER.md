# Handover

## Start here

Read `README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, then `AGENTS.md`.

## Experimental history

`app/phase2_*` through `app/phase13_*` are retained as evidence of tests and
failures. Do not extend numbered phase scripts. New work belongs in
`app/providers/`, then future `app/audio/` and `app/pipeline/` modules.

## Known findings

- Gemini 3.6 Flash audio timestamps are not trusted for subtitles.
- Chirp 3 works as word-timing source on compliant chunks.
- Whole 55:49 file with word timestamps is invalid; chunk it.
- GCS `BatchRecognizeFileResult` old fields are deprecated.

## Required acceptance evidence

Raw GCS JSON, per-chunk manifests, pre/post-merge words, join QA, fixed
segments, Gemini raw responses, correction results, QA report, and a user
approval before Drive upload.

## Current implementation status

- `app/providers/chirp_chunk.py`: Chirp submit/poll path with `SUBMIT_ONLY`
  support.
- `app/providers/recover_chunk.py`: GCS-only recovery, including explicit patch
  roles; never re-submits a completed chunk.
- `app/providers/merge_chunks.py`: authoritative midpoint merge plus targeted
  patch replacement and ±10-second join QA.
- `app/providers/build_srt.py`: fixed immutable segment IDs and raw
  SRT/VTT/TXT/Markdown exports.
- `app/providers/correct_text.py`: global terminology extraction followed by
  `gemini-3.6-flash` text-only correction per fixed subtitle window.
- `app/providers/export_formats.py`: deterministic local ASS and segment CSV
  exports; no external upload.
- `app/providers/validate_outputs.py`: read-only end-to-end artifact verifier.
- `app/api.py` and `frontend/`: loopback-only, read-only review workspace. It
  exposes only an allowlist of derived artifacts and never mounts credentials.

The current GitHub branch is `agent/frontend-api-integration`; keep its draft
PR until a human accepts the local outputs. The formal job currently passes
strict QA and output validation, but still requires human content review before
any Drive upload. Never use any numbered historical `phase*` script for new
work.
