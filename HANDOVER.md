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
