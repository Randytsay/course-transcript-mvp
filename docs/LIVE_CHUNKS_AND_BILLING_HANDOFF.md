# Live Chunks and Billing Handoff

## Architecture & Implementation

### 1. Parallelism Control
- `chirp_max_parallel_chunks` is now a formal database column in `jobs` (SQLite `INTEGER NOT NULL DEFAULT 3`).
- API (`CreateJobRequest`, `CreateBatchRequest`) accepts this setting (min 1).
- Server-side enforces `CHIRP_MAX_PARALLEL_CHUNKS_LIMIT` (default 5).
- Frontend allows selecting 1-5 chunks concurrent limits.
- `app.pipeline.worker` translates the job setting into an environment variable `CHIRP_MAX_PARALLEL_CHUNKS` passed to the `run_chirp_pipeline.py` subprocess.
- `run_chirp_pipeline.py` computes chunk boundaries using the actual audio duration dynamically (15-min chunks, 10s overlap).

### 2. Live Chunk Transcripts (3-Tier View)
- `chirp_chunk.py` outputs a `partial-transcript.json` upon completion containing concatenated raw text.
- API endpoints `/api/v1/jobs/{id}/chunks` and `/api/v1/jobs/{id}/chunks/{idx}/transcript` supply granular progress without massive JSON blobs.
- UI queries chunks every 3 seconds (aborts and halts when hidden) but avoids downloading full transcripts until user actively clicks "展開原始稿" (Expand Original Text).
- "Formal Transcript" tier remains separate and clearly indicates unavailability until final text correction and QA are verified.

### 3. Live Cost & GCP Billing Summary
- **Live Cost:** API dynamically sums actual duration chunks (including overlap) matched against `costs.py` pricing tables. Accurate usage is extracted from `correct-work/` window caches.
- **GCP Billing:** Isolated `billing-worker` process (Docker optional component) continuously queries `bigquery_standard_billing_export` data every 15 minutes.
  - Generates atomic `billing_snapshot.json`.
  - Frontend warns if data is stale (over 1 hour old).
  - TWD/USD conversion uses BigQuery's `currency_conversion_rate`.

## Operational Safety Boundaries
- **Billing API is strictly read-only.** It produces JSON metrics, avoids API errors impacting the dashboard, and never attempts to rewrite IAM or Billing configurations.
- **Zero Real Traffic Execution.** `course-transcript-infrastructure-test:local` validates syntax and component dependencies without executing `Chirp` or `Gemini` API traffic on testing.
- BigQuery inputs are parameterized using `@target_project` and `@start_date` standard scalar markers.