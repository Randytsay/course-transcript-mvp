# Handover

## Start here

Read `README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, `DEPLOYMENT.md`, `docs/DYNAMIC_BATCH_AND_SUBTITLE_EDITOR_HANDOFF.md`, and `docs/VPS_DEPLOY_GATE.md`, then `AGENTS.md`.

## Repository state

- Production branch: `main`.
- PR #8 (`fix: harden dynamic batching, delivery retries, and subtitle publishing`) was merged on 2026-08-02.
- PR #8 merge commit: `e5acbf73aff14e55b790bd65a19cb822a1dbb17e`.
- The production entry points are:
  - API: `app.api_hardened:app`
  - paid pipeline worker: `app.pipeline.dynamic_worker_production`
  - delayed Drive delivery worker: `app.jobs.delivery_worker`
- The old `agent/frontend-api-integration` / Draft PR #2 / commit `5d4ed4d` references are historical only and must not be used for deployment.

## Experimental history

`app/phase2_*` through `app/phase13_*` are retained as evidence of tests and failures. Do not extend numbered phase scripts. New work belongs in `app/providers/`, `app/jobs/`, `app/subtitles/`, and `app/pipeline/`.

## Known findings

- Gemini 3.6 Flash audio timestamps are not trusted for subtitles.
- Chirp 3 is the authoritative word-timing source on compliant chunks.
- Long audio with word timestamps must be chunked according to the current planner; do not assume every chunk is exactly 15 minutes.
- GCS `BatchRecognizeFileResult` old fields are deprecated.
- Dynamic batching is non-blocking: submitted jobs remain `status=transcribing`, `active_stage=chirp` while the worker lease is released.
- Successful hardened jobs finish as `completed`; `awaiting_review` is retained only for compatibility with older jobs and delivery recovery.

## Current implementation

- `app/providers/chirp_chunk_hardened.py`: submits one durable Chirp operation with an attempt-isolated GCS prefix.
- `app/providers/recover_chunk_hardened.py`: recovers one saved operation, distinguishes pending/retryable/terminal outcomes, applies provider deadlines, persists local evidence before GCS cleanup, and never re-submits a retained operation.
- `app/providers/run_chirp_pipeline_hardened.py`: preserves compatible retained standard/dynamic plans and merges only after every chunk is recovered.
- `app/pipeline/dynamic_worker_production.py`: production paid runner with leases, non-blocking dynamic submission, retained-standard compatibility, actual-strategy accounting, restart resume, and locked Drive publication.
- `app/providers/build_srt.py`: lexical Chinese subtitle segmentation with immutable timings.
- `app/providers/correct_text_hardened.py`: `gemini-3.6-flash` text-only correction, 60-second windows, immutable per-attempt audit evidence, adaptive split, and severe-drift fallback to raw Chirp text.
- `app/providers/validate_outputs_hardened.py`: structural validation plus content-drift QA; `content-qa.json` is checksummed in the export manifest.
- `app/providers/subtitle_cleanup.py`: deterministic high-confidence filler/stutter cleanup with immutable timing and a `cleanup-review.json` manual-review list.
- `app/jobs/drive_publish.py`: resumable pending/verify/backup/promote/final-verify publication transaction.
- `app/jobs/delivery_worker.py`: retries Drive delivery from existing local artifacts only; it never repeats Chirp or Gemini.
- `app/jobs/drive_lock.py`: cross-process global Drive lock and shared cooldown. It requires all participating containers to mount the same host `./data` directory at `/app/data`.
- `app/subtitles/editor_hardened.py`: strict SRT import, revision-gated editor publication, editor-intent persistence, and protection against delayed pipeline overwrite.
- `app/api_hardened.py`: installs the hardened subtitle mutation routes without duplicating the read/edit routes.

## Required acceptance evidence

Retain raw GCS JSON, operation and chunk manifests, pre/post-merge words, join QA, fixed segments, every Gemini raw response and usage record, correction results, `content-qa.json`, export manifest, QA report, Drive publication state, and job event history.

Do not delete evidence merely because a user-facing format was not selected.

## Deployment status

GitHub CI is complete. Production VPS deployment and real-provider validation are separate gates.

Before deployment:

1. Confirm no paid job is active and no relevant lease is unexpired.
2. Back up `data/course-transcript.db` and the complete `data/jobs/` tree.
3. Confirm `api`, `pipeline-worker`, and `delivery-worker` render the same host source for `/app/data`.
4. Build ARM64 images on the Oracle host.
5. Run non-paid health, import, Compose, and restart-persistence checks.
6. Stop before real Chirp, Gemini, or Drive mutation tests and request explicit approval.

Follow `docs/VPS_DEPLOY_GATE.md` exactly and return its requested evidence summary. Never print service-account JSON, rclone configuration, OAuth tokens, Cloudflare tunnel tokens, or secret environment values.
