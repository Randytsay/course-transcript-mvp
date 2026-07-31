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
- `app/providers/build_srt.py`: lexical Chinese subtitle segmentation using
  local `jieba`, then fixed IDs and raw SRT/VTT/TXT/Markdown exports. It never
  splits a lexical unit solely to meet a duration target.
- `app/providers/correct_text.py`: global terminology extraction followed by
  `gemini-3.6-flash` text-only correction per fixed subtitle window.
- `app/providers/export_formats.py`: deterministic TXT/SRT/VTT/JSON/CSV/
  DOCX/PDF exports with read-back hashes; no external upload.
- `app/providers/validate_outputs.py`: read-only end-to-end artifact verifier.
- `app/api.py` and `frontend/`: private workspace with Drive browse, one/many/
  recursive-folder batch selection, preflight, exact-cost approval, live
  status/events, pause/resume, failed-stage retry, transcript/term/QA review,
  and allowlisted downloads.
- `app/jobs/source.py`: strict rclone path validation, one-level private Drive
  browsing, explicit multi-file preview, and bounded recursive folder preview.
- `app/jobs/store.py`: SQLite WAL batches/jobs, immutable previews, revision
  gates, US$200 estimate reservation, global one-source lease and heartbeat.
- `app/jobs/preflight.py`: sequential, non-paid rclone copy/checksum/FFprobe
  worker; removes the temporary source before awaiting cost approval.
- `frontend/app/batches/[id]`: live preflight status and explicit whole-batch
  paid-operation authorization.
- `app/pipeline/worker.py`: approved-job-only paid runner with global lease,
  heartbeat, evidence-based resume, idempotent usage, complete local exports,
  and a hard stop at `awaiting_review`.
- `app/providers/fake_timeline.py` and `fake_correction.py`: non-paid provider
  substitutes for Docker/VPS integration tests.

The current GitHub branch is `agent/frontend-api-integration`; keep its draft
PR until a human accepts the local outputs. The formal job currently passes
strict QA and output validation, but still requires human content review before
any Drive upload. Never use any numbered historical `phase*` script for new
work.

At this handoff the local and ARM64 suites pass, the VPS Compose services are
running, the non-paid fake E2E passes, restart persistence is proven, the
frontend-to-API Drive browse path works, and the public hostname redirects an
unauthenticated request to Cloudflare Access. The remaining human check is a
refresh in the already authenticated browser followed by selection/preflight
of a small file. Then stop for explicit confirmation of that source, exact
estimate, models, and paid-test approval. Never click the cost authorization
or start a real request merely because a job page is visible.

Deployed Git evidence: branch `agent/frontend-api-integration`, commit
`5d4ed4d`, Draft PR #2. See `docs/DEPLOYMENT_STATUS.md`.
