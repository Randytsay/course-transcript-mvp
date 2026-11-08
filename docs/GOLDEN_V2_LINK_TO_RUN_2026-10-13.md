# Drive link to durable V2 intake — development checkpoint

Implemented in the isolated `/private/tmp/golden-v2-link-to-run` checkout, on
PR #144's `7f6da90` baseline. Original dirty Mac checkout preserved. This is
source-level integration, not production acceptance. Owner clarified in this
chat that V2 remains Gemini 3.8 audio primary plus Chirp 3 timing.

## New shared owner API

- `POST /api/v1/golden-v2/link-preview`: status token; Drive GET only. Supports
  normal HTTPS Drive folder/file/open links, optional recursive listing,
  fresh permitted-root ancestry, pagination, ID deduplication, duplicate-name
  rejection and stable preview SHA. Resource-key links and shortcuts are
  explicitly unsupported. Defaults: 100 media files, 2 GiB/file, 10 GiB total,
  five descendant levels, 2,000 entries and 100 pages. Limit/incomplete-search
  failures reject the preview, rather than silently accepting a partial list.
- `POST /api/v1/golden-v2/link-batches`: separate write token; explicit confirmed
  preview SHA, mode and exact per-file/per-stage estimates. Re-lists Drive
  before SQLite transaction. A changed preview returns conflict. It persists
  local intake only; no download or provider request occurs in this endpoint.
- `GET /api/v1/golden-v2/link-batches/{batch_id}`: status token; reads batch items,
  intake states, child job IDs and current child ledger states without migration.

`v2_link_batches`, `v2_link_batch_items` and `v2_intake_sources` live in the same
isolated SQLite file as `v2_jobs`. Batch replay returns the same ID. Overlapping
batches share a source intake keyed by Drive ID/version/mode. Conflicting path,
checksum or estimates for that identity require review; they do not trigger a
second download/job. A failed source has a durable safe error code and is not
reported as successful. Explicit retry controls remain to be implemented.

## Non-paid worker and web entry

The `/golden-v2` console now previews links, accepts operator estimates, creates
persistent intake batches and polls batch status. The last batch ID is retained
locally for resuming the page; bearer tokens never enter browser JavaScript.
The new Next.js proxies retain feature flag, admin host and same-origin guards,
bounded request bodies, server-only scoped tokens and no-store responses.

`scripts/golden_v2_batch_intake_daemon.py` processes one pending source at a time,
uses the existing authenticated downloader and FFmpeg preflight, and leaves new
jobs `AWAITING_APPROVAL`. It checks exact preview ID/version/size/MD5/path against
the completed download before registration. A killed non-paid intake can resume;
paid stages are not reset. Cross-process locks serialize batch daemons and both
single-file and batch source importers. Busy single-file imports leave queued
batch work pending. Docker now copies `scripts/`, required by both daemon entry
points. The separate `golden-v2-intake` Compose profile mounts Drive configuration
read-only and shares `./data`; it has no provider credential mount. It remains
unstarted on VPS.

Estimates are still explicitly operator-supplied. Preview has no duration and
returns `estimated_cents=null`, `DURATION_PREFLIGHT_REQUIRED`. The new UI leaves
unknown estimates blank. Trusted automated provider pricing/duration estimates
remain a release prerequisite. Queue creation is not paid budget approval.

## Trusted operator and provider controller

`scripts/golden_v2_owner_client.py` supports preview, create-batch, batch-status,
jobs, job-status and revision-bound approve-job against the same API. Tokens
come from server environment, never command arguments. Only HTTPS origins or
loopback/container HTTP are allowed; redirects and automatic retries are
forbidden. Writes require confirmed bounded JSON files. This is a callable CLI,
not an installed ChatGPT connector.

`run_paid_executor_pass` plus `scripts/golden_v2_paid_executor_daemon.py` provide
a disabled-by-default persistent controller around existing guarded adapters
and local coordinator. Trusted runtime factories must supply independent owner,
capability/pricing checks, real stage clients and operation-bound billing.
Every fresh dispatch uses the existing source/revision/stage/aggregate-budget
CAS. Saved running Chirp operations use the existing recovery pass, never a
new claim. Unknown paid outcomes remain held across cycles. Errors do not leak
provider messages and do not automatically resubmit. The daemon's enable flag
is not owner approval; no permissive production verifier/factory is supplied.
The real credential/pricing/SDK runtime integration and VPS installation remain
unfinished. V1's `google-genai<2` requirements are unchanged; successful smoke
used a separate 2.29.0 environment. Do not run enterprise clients in the V1 image
or call the generic controller production-ready before runtime verification.

## Verification and observed infrastructure

- Browser: local fixed-data backend, actual FastAPI routes/SQLite/Next.js proxy.
  Foreign link rejection, preview, batch creation and page reload/ID continuity
  verified. No real Drive/model request. Screenshot:
  `docs/evidence/golden-v2-link-intake-local-fixture.png`.
- A local WAV with simulated authenticated Drive download was passed through
  actual FFmpeg and registered in actual SQLite with immutable original/FLAC;
  stayed unapproved. Mock transport is not real Drive acceptance.
- Focused tests cover paginated discovery, root cache distrust, oversized and
  incomplete listings, deduplication, preview/version changes, restart and
  process locks, API scope isolation, default-off paid controller, exact CAS,
  timeout non-resubmission, and saved Chirp recovery delegation.
- Local final regression: **1032 unittest PASS**, **27 handoff pytest PASS**;
  **64 frontend tests PASS**, Next.js production build PASS and production
  dependency audit 0 vulnerabilities. Python compile/import, three Compose
  profiles and diff check PASS. Exact-SHA GitHub CI must be checked after push.
  Earlier baseline was 1007 Python tests and 62 frontend tests.
- Read-only VPS check in this turn: production services all retain
  `adafd952b9b89db111730ab28e8e8baff9551021`; separate V2 realtest/canary containers
  retain older test SHAs. No production replacement or flag activation occurred.

## Remaining launch work

1. Trusted automated estimates and real provider service factories/SDK image.
2. Actual ChatGPT/WebCodex tool deployment and authenticated endpoint access.
3. Review UI, full acoustic/semantic Golden acceptance and publication adapter.
4. Complete backups/restore, historical operation reconciliation, staging,
   ARM64/persistence/restart/canary/rollback and G0–G8 evidence.
5. Exact acceptance Drive source, reviewed costs, scoped publication approval.

This development iteration made no paid provider requests, GCS uploads, Drive
writes, IAM/firewall changes, or production service changes. Do not mark the
service launched from these local tests or from CI alone.
