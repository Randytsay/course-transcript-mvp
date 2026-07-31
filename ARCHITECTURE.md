# Architecture

## Runtime boundary

- Oracle VPS: Docker, FFmpeg, persistent job data and orchestration.
- Google Drive: source and only user-approved final-output destination.
- GCS: private input chunks and raw Chirp result JSON.
- Chirp 3 in `us`: word timestamps and subtitle timing.
- Google Vertex AI Gemini 3.6 Flash: text-only correction, never subtitle timing.

## Web batch boundary

The frontend can select explicit media files or recursively preview one Drive
folder. This is an operator-triggered, read-only rclone listing; it is not a
Drive scanner. A batch preview is immutable, expires after 30 minutes, and can
be consumed once.

SQLite stores `batch_previews`, `source_previews`, `batches`, `jobs`,
`job_events`, `stage_runs`, and `usage_records`. Each media file is an
independent resumable job. A batch may contain many preflight/queued jobs, but
the lease manager permits only one unexpired source-processing lease globally.

The preflight worker has rclone and FFprobe but no GCP credential mount. It
copies one source into a controlled temporary directory, records SHA-256,
duration, format and codec, calculates the application-side estimate, then
removes the temporary source. Only a revision-checked, exact-total batch
approval may reserve cost and move jobs to `queued`.

```text
Cloudflare Access
  → Next.js (only tunnel target)
  → FastAPI
     ├─ read-only rclone browse/preview
     └─ SQLite batches/jobs/cost ledger
  → sequential Worker
     ├─ non-paid preflight
     └─ approved pipeline worker
        ├─ Drive copy/checksum + FFmpeg normalization
        ├─ chunk-000 canary, then ≤3 parallel Chirp chunks
        ├─ midpoint merge + immutable subtitle segments
        ├─ Gemini 3.6 Flash text-only correction
        └─ local exports/QA/checksums → awaiting_review
```

## Long-file contract

Chirp 3 word timestamps limit each BatchRecognize source file to 20 minutes.
The production target uses 15-minute chunks with 10-second overlap. A chunk is
successful only after its GCS JSON, results, alternatives, words, monotonic
timestamps, and manifest are present.

## Runtime and review boundary

`voice_11386603-seg1` is retained as the first formal long-file evidence
(55:49.345).
It completed Chirp 3 base chunks 000–004 plus five targeted Chirp patch chunks
005–009. The authoritative merge contains 16,898 valid words and 862 fixed
subtitle segments. The final tail is 105 ms short of FFprobe duration, within
the local output QA threshold; it is not a transcription gap.

Chirp patch chunks replace words only inside their exact source windows. Base
chunks own all other words by midpoint ownership. This preserves raw GCS JSON,
allows repair of real speech gaps without re-sending the whole recording, and
prevents text-similarity merging from deleting words.

The final reading subtitle layer groups Chirp's character-level timing into
Traditional-Chinese lexical units before selecting sentence and pause
boundaries. It never splits a lexical unit such as `時間`; it preserves the
underlying Chirp timings and has no Gemini timing input. Gemini correction is
complete using `gemini-3.6-flash` only: 120 raw per-window responses, one
global terminology record, and 654 corrected segments across 1,103 readable
subtitle cues. Do not upload to Drive until QA and explicit user approval.

The web API never performs Drive upload. Mutation requests require Cloudflare
Access identity plus the exact production Origin. The API and workers share a
SQLite WAL file; only the pipeline worker receives the read-only GCP credential
mount. A global lease permits one active source job while up to three chunks of
that source may run in parallel.

See `docs/DATABASE_SCHEMA.md`, `docs/STATE_MACHINE.md`, and `docs/API.md` for
the durable contract.
