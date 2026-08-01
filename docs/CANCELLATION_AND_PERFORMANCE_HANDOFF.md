# Cancellation and performance observability handoff

This branch is stacked on `feature/live-chunks-billing-controls` and PR #5. It must not be merged into `main` before PR #5 is reviewed and the stacked diff is rebased or retargeted.

## Scope

The change adds:

- pause, resume, permanent cancellation, and cancellation audit events;
- cooperative process-group termination for local preflight and paid pipeline commands;
- best-effort cancellation requests for submitted Speech-to-Text v2 operations;
- optional deletion of large temporary audio while preserving provider evidence;
- per-stage-attempt, per-Chirp-chunk, and per-Gemini-call performance records;
- a performance API and JSON, CSV, and HTML reports;
- a responsive, high-contrast frontend for controls and performance review;
- bounded concurrent recovery of private GCS Chirp results.

It does not modify IAM, Cloud Billing, Cloudflare, DNS, firewall, Drive publishing, or production secrets.

## Job state model

Pause and cancellation have different semantics.

```text
queued/running -> paused -> queued
queued/running/paused/failed/preflight -> cancelling -> cancelled
```

Jobs with no submitted provider operation can move directly to `cancelled`. A paused or failed job that still has a non-terminal Speech operation moves to `cancelling` so the pipeline worker, which owns the GCP credential, can issue the provider cancellation request.

`cancelled` is terminal. A cancelled job cannot be resumed. Reprocessing requires a new job.

## Cancellation API

```http
POST /api/v1/jobs/{job_id}/cancel
Content-Type: application/json
```

```json
{
  "expected_revision": 12,
  "reason": "來源檔選錯",
  "cleanup_mode": "preserve"
}
```

`cleanup_mode` values:

- `preserve`: retain all local files and provider evidence.
- `temporary`: delete large source/normalized/chunk audio and `.tmp`/`.partial` files after cancellation; retain manifests, word JSON, provider raw results, costs, and audit evidence.

The endpoint is revision protected. A stale page receives HTTP 409 and must reload before retrying.

## Cloud cancellation limitations

Speech operation cancellation is best effort. A request can race with provider completion, may be unsupported for a specific operation, and does not imply a refund. The program records one of:

- `requested`
- `unsupported`
- `error`

Already submitted audio is counted as committed estimated cost. Google Cloud Billing remains authoritative.

The API container does not receive the GCP service account. Provider cancellation runs only in the pipeline worker. This preserves the existing credential boundary.

## Worker behavior

### Preflight worker

`app.jobs.preflight_observed` replaces the prior command in Compose. It uses a process group for the long rclone copy and checks cancellation once per second. It never calls a paid model.

### Paid pipeline worker

`app.pipeline.worker_observed`:

- checks pause/cancel state approximately every two seconds;
- terminates the complete subprocess group, not only the direct Python child;
- retains the existing 15-second lease heartbeat;
- finalizes cancellation for a currently running job;
- also searches for orphan `cancelling` jobs after a restart and completes provider cancellation before selecting another paid job.

## Performance schema

The existing `stage_runs` table remains unchanged for backward compatibility. A new table is created lazily:

```text
performance_stage_attempts
- job_id
- stage
- attempt_number
- status
- started_at
- completed_at
- active_duration_ms
- error
```

Each retry creates a new row. Old jobs fall back to the existing `stage_runs` data.

No destructive database migration is required.

## Chirp metrics

Each chunk manifest can contain:

- `attempt_count`
- `chunk_started_at`
- `audio_ready_at`
- `upload_completed_at`
- `request_started_at`
- `submitted_at`
- `submit_latency_ms`
- `provider_completed_at`
- `provider_processing_ms`
- `recovery_started_at`
- `recovered_at`
- `recovery_delay_ms`
- `recovery_download_ms`
- `total_wall_ms`
- `word_count`

Chirp does not use LLM tokens. Its cost is estimated from the actual submitted chunk duration, including overlap.

## Gemini metrics

The glossary request and each correction window store:

- model and prompt version;
- request and response timestamps;
- latency;
- attempt count and retry event types;
- input and output tokens from `usage_metadata`;
- source subtitle time range for correction windows;
- whether a prior evidence file was reused.

Metrics use the API-call/window level because multiple subtitle segments share one context window.

## Performance API

```http
GET /api/v1/jobs/{job_id}/performance
```

The response includes:

- source audio duration;
- total elapsed, queue, pause, and wall-processing times;
- sum of stage-attempt active durations;
- RTF and active RTF;
- estimated accrued cost and cost per audio hour;
- stage attempts and totals;
- Chirp chunk metrics;
- Gemini call metrics;
- deterministic bottleneck suggestions.

RTF is:

```text
wall processing time / source audio duration
```

A value below 1 means the job completed faster than real-time playback. Queue and user pause intervals are excluded from wall processing.

## Downloadable reports

```http
GET /api/v1/jobs/{job_id}/performance-report.json
GET /api/v1/jobs/{job_id}/performance-report.csv
GET /api/v1/jobs/{job_id}/performance-report.html
```

The worker writes the reports after completion, pause, cancellation, or failure. The API can also regenerate them from durable evidence.

## Concurrency changes

The first Chirp chunk remains a serial canary. Remaining submissions use the job's configured parallelism. GCS result recovery is now independently bounded by:

```env
CHIRP_MAX_PARALLEL_RECOVERY=3
```

It does not poll Speech LRO status. It checks private GCS output and does not resubmit retained operations.

Gemini correction windows remain behind a global terminology call and are bounded by:

```env
GEMINI_MAX_PARALLEL_WINDOWS=2
GEMINI_CORRECTION_WINDOW_MS=30000
```

Increase these values only after comparing P50/P95 latency, retries, 429 rate, human correction rate, and cost per audio hour.

## Frontend

The job page includes:

- Pause when a task is queued or processing.
- Resume when paused.
- Permanent Cancel for eligible states.
- A confirmation dialog with reason and optional temporary cleanup.
- Explicit warning that cloud cancellation is not guaranteed and costs may remain.

The performance page is available at:

```text
/jobs/{job_id}/performance
```

It supports the existing Standard, Large, and X-Large typography modes and collapses cards for mobile use.

## Required validation before deployment

Run on the branch:

```bash
python3 -m compileall -q app tests
python3 -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev
docker compose --profile web config --quiet
docker compose --profile billing config --quiet
sudo docker compose --profile web build
```

On Oracle ARM64, use the fake provider and a copied database/work directory. Validate:

1. cancel before cost approval;
2. cancel while queued;
3. pause and resume during a long local command;
4. cancel during a fake Chirp stage;
5. restart the worker with a job left in `cancelling`;
6. preserve and temporary-cleanup modes;
7. stage-attempt rows after retry;
8. performance JSON/CSV/HTML generation;
9. mobile and X-Large typography;
10. no real Speech, Gemini, BigQuery, or Drive write operation.

## Production rollout

Before restart:

1. confirm there is no active paid job;
2. back up `data/course-transcript.db` and the current job directories;
3. keep all existing secret paths read-only;
4. build ARM64 images;
5. start with Billing disabled unless it has already been configured separately;
6. verify API and frontend health checks;
7. execute a fake-provider acceptance job;
8. only then permit a small real job after explicit cost approval.

## Rollback

This feature adds a new table but does not alter existing columns. Older code ignores the additional table and performance files.

Rollback procedure:

1. stop new job creation;
2. ensure no job is `cancelling`;
3. preserve `data/`, `logs/`, and secrets;
4. checkout the previous reviewed commit;
5. rebuild API, preflight worker, pipeline worker, and frontend;
6. restart the web profile;
7. do not delete `performance_stage_attempts` or generated reports.

A job already marked `cancelled` should remain historical and must not be manually changed back to `queued`.
