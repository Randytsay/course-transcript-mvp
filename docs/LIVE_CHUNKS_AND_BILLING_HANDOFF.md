# Live Chunks, Per-Job Parallelism, and Billing Handoff

This document describes the reviewed implementation on branch
`feature/live-chunks-billing-controls`. It does not authorize a production
merge or VPS restart by itself. GitHub CI and an Oracle ARM64 build must pass
first.

## Scope

The feature adds three independent capabilities:

1. Per-job Chirp chunk parallelism, defaulting to 3 and bounded by a server
   limit.
2. A three-tier task page: chunk progress, expandable live Chirp raw text, and
   a gated formal transcript.
3. Application-side live cost estimates plus an optional, read-only Cloud
   Billing BigQuery snapshot.

It does **not** enable automatic Drive publishing, modify IAM, create a
BigQuery dataset, enable billing export, or execute paid provider tests.

## Runtime entry points

- API: `uvicorn app.api_final:app`
- Preflight worker: `python -m app.jobs.preflight`
- Paid pipeline worker: `python -m app.pipeline.worker`
- Optional billing worker: `python -m app.billing.worker`
- Frontend: existing Next.js standalone server

`app.api_final` retains the stable API, replaces prototype live endpoints with
reviewed handlers, and gates `/segments` until formal output is complete.

## 1. Chirp parallelism control

### Persistence

SQLite `jobs` contains:

```sql
chirp_max_parallel_chunks INTEGER NOT NULL DEFAULT 3
```

`JobStore.initialize()` uses `_ensure_column`, so existing databases are
upgraded without deleting data. Existing jobs receive the default value 3.
Batch creation copies the selected value to every child job.

### API contract

Both create endpoints accept:

```json
{
  "chirp_max_parallel_chunks": 3
}
```

The field must be a JSON integer. Values below 1 or above the server limit are
rejected with HTTP 422. Strings and decimals are rejected.

Environment variables:

```text
CHIRP_MAX_PARALLEL_CHUNKS=3
CHIRP_MAX_PARALLEL_CHUNKS_LIMIT=5
```

The first value is the UI/default value. The second is authoritative. The
pipeline worker computes the effective value from the stored job and the
server limit, then passes only that value to `run_chirp_pipeline.py`.

The existing safety behavior remains:

- chunk 0 runs as the Canary;
- only after Canary success are remaining chunks run concurrently;
- successful and `EMPTY_SILENCE` chunks are not resubmitted;
- `SUBMITTED` chunks first use the recovery path;
- source files are still processed one at a time.

## 2. Three-tier task page

### Tier 1: chunk progress

Endpoint:

```http
GET /api/v1/jobs/{job_id}/chunks
```

The canonical list comes from `chunk-plan.json`, not only existing manifest
folders. Therefore all planned chunks appear immediately and keep a stable
`totalCount`. A chunk manifest overrides its current status.

Stable status enums:

```text
WAITING
SUBMITTED
RUNNING
RECOVERING
SUCCEEDED
EMPTY_SILENCE
FAILED
```

The frontend translates these enums into Traditional Chinese. The API does not
return provider GCS URIs, operation names, credential paths, or raw provider
errors. Browser-visible errors are redacted.

### Tier 2: live Chirp raw text

Endpoint:

```http
GET /api/v1/jobs/{job_id}/chunks/{chunk_index}/transcript
```

The endpoint is available only when the current manifest is `SUCCEEDED` or
`EMPTY_SILENCE`. It rebuilds and atomically stores
`partial-transcript.json` from the durable `words.json` evidence. Mixed
Chinese, English, numbers, and punctuation are spaced for readability.

The browser fetches raw text only when the user expands a completed chunk. It
caches loaded text locally and does not download all text on every poll.
Adjacent chunks remain visually separate because they contain a 10-second
overlap.

### Tier 3: formal transcript

Endpoint:

```http
GET /api/v1/jobs/{job_id}/segments
```

Formal output returns HTTP 409 until all required evidence exists.

With Gemini correction enabled:

- all planned chunks are `SUCCEEDED` or `EMPTY_SILENCE`;
- `merged-words.json` exists;
- `subtitles.json` exists;
- `subtitles-corrected.json` exists;
- `qa-report.json` exists;
- job status is `awaiting_review`, `review`, or `completed`.

Without Gemini correction, `subtitles-corrected.json` is not required.

The live page is `/jobs/{id}`. The pre-existing full review, terminology, and
artifact workspace remains available at `/jobs/{id}/review`.

### Polling strategy

The live task page:

- polls job, chunk, and live-cost summaries about every 3 seconds;
- uses a real `AbortController` signal;
- prevents overlapping requests;
- stops in hidden tabs;
- refreshes immediately when the tab becomes visible;
- stops high-frequency polling for paused or terminal jobs;
- exponentially backs off on errors up to 30 seconds;
- does not use WebSocket or SSE.

## 3. Application live-cost estimate

Endpoint:

```http
GET /api/v1/jobs/{job_id}/live-cost
```

It returns:

```json
{
  "estimatedTotalUsd": "1.42",
  "estimatedAccruedUsd": "0.73",
  "estimatedRemainingUsd": "0.69",
  "chirpEstimatedUsd": "0.62",
  "geminiEstimatedUsd": "0.11",
  "submittedChunkCount": 2,
  "completedChunkCount": 1,
  "isEstimate": true
}
```

Chirp cost uses actual durations from `chunk-plan.json` for chunks in committed
provider states. Overlap is therefore included. Each index is counted once.
Gemini cost reads actual usage metadata from:

```text
glossary/global-terms.json
correction-v2/*.json
```

All rates come from `CostConfig`; the browser contains no pricing formula.
Cloud Billing remains authoritative.

## 4. Optional GCP billing summary

### Architecture

```text
Cloud Billing standard export
  -> BigQuery
  -> dedicated read-only billing service account
  -> billing-worker every 15 minutes
  -> data/billing/billing_snapshot.json
  -> FastAPI read-only summary
  -> dashboard cards
```

The normal `web` Docker profile does not start or depend on billing. The
billing worker uses the separate `billing` profile.

Start it only after manual setup:

```bash
docker compose --profile billing up -d billing-worker
```

### API

```http
GET /api/v1/billing/summary
```

Possible statuses:

```text
disabled  billing feature not enabled
pending   enabled but no first snapshot yet
ok        current successful snapshot
stale     old successful snapshot retained after age/failure
error     no successful snapshot and synchronization failed
```

### Query scopes

The query deliberately separates:

- project gross cost, project credits, and project net cost: filtered by
  `BILLING_TARGET_PROJECT_ID`;
- account promotional credits: all projects in the billing export;
- free-trial credit use: only PROMOTION credits whose id/name/full name matches
  `BILLING_PROMOTION_NAME_PATTERN`.

Credits are aggregated in correlated subqueries, so unnesting credits cannot
multiply `cost` rows.

### Currency behavior

The billing currency comes from BigQuery and is never hard-coded. If the query
contains multiple currencies, local-currency summary fields are not shown. If
any relevant row lacks `currency_conversion_rate`, USD conversions and the
estimated free-trial remainder are returned as unavailable. TWD and USD are
never subtracted directly.

`lastBillingDataAt` is the latest usage timestamp in the export, while
`snapshotGeneratedAt` is the local worker time. The UI labels Cloud Billing
data as delayed, not real time.

### Remaining free-trial credit

The API cannot retrieve an official free-trial balance. It estimates:

```text
configured initial USD credit - matched free-trial PROMOTION credits used
```

The UI must continue to state that Billing Overview is the official source.

## Configuration

See `.env.example`. Billing remains disabled by default.

```text
BILLING_ENABLED=false
BILLING_BIGQUERY_PROJECT=
BILLING_BIGQUERY_DATASET=
BILLING_BIGQUERY_TABLE=
BILLING_TARGET_PROJECT_ID=course-transcript-mvp
BILLING_TRACKING_START_DATE=2026-07-01
BILLING_INITIAL_FREE_TRIAL_CREDIT_USD=300
BILLING_PROMOTION_NAME_PATTERN=Free trial
BILLING_SYNC_INTERVAL_SECONDS=900
BILLING_SNAPSHOT_STALE_SECONDS=3600
BILLING_MAXIMUM_BYTES_BILLED=1000000000
BILLING_CREDENTIALS_HOST_PATH=/opt/course-transcript/secrets/billing-sa.json
```

Project, dataset, and table identifiers are allowlist-validated before being
inserted into SQL. Values such as project and date use query parameters.

## Security boundaries

- Service-account JSON is never returned by any endpoint.
- Frontend never connects directly to BigQuery, GCS, Speech, Vertex, or Drive.
- Billing is read-only and has no IAM, budget, export, or payment mutation API.
- Provider error text is redacted before browser display.
- Billing failures cannot fail the transcription API health check.
- Drive publishing remains disabled.
- Tests use fake providers or mocks and must not call paid APIs.

## CI and local validation

Required before review:

```bash
python3 -m compileall -q app tests
python3 -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev
docker compose --profile web config --quiet
docker compose --profile billing config --quiet
```

GitHub Actions needs no GCP credentials and must not execute real provider
requests.

## Oracle ARM64 validation

After CI passes, Codex may validate the branch on the VPS without activating a
paid job:

```bash
git fetch origin
git checkout feature/live-chunks-billing-controls
python3 -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend run build
sudo docker compose --profile web build
sudo docker compose --profile web up -d
sudo docker compose --profile web ps
curl -fsS http://127.0.0.1:3300/api/v1/health
```

Before restarting, confirm there is no active paid source job and back up
`data/course-transcript.db`. Do not start the billing profile until its manual
GCP setup and credential file are complete.

## Rollback

No destructive migration is used. Preserve `data/`, `logs/`, `tmp/`, and all
secret files, then check out the previous reviewed commit and rebuild the
`web` profile. The added SQLite column is backward-compatible and can remain in
place.

## Manual steps not performed by this branch

- enabling Cloud Billing standard export;
- creating or selecting the BigQuery dataset;
- creating the dedicated read-only billing service account;
- placing its credential on the VPS;
- setting real billing table names;
- starting the billing profile;
- Oracle ARM64 deployment;
- any real paid Chirp, Gemini, or BigQuery acceptance request;
- merging to `main`.
