# Production optimization and acceptance plan

This document separates changes that are safe to implement in code from checks
that require explicit approval because they can incur provider charges or mutate
Google Drive.

## 1. Supported processing modes

The production worker currently supports Google Speech-to-Text V2 Chirp 3
Dynamic Batch processing. Dynamic Batch is the default economical mode: work is
submitted durably, the worker lease is released, and recovery resumes from the
saved operation evidence. It must never be described as a scheduled midnight
job; Google decides when spare capacity is available.

Recommended product labels:

| Product label | Provider strategy | Intended use | Completion expectation |
| --- | --- | --- | --- |
| Economical | `DYNAMIC_BATCHING` | Courses and other non-urgent audio | Allow up to 24 hours |
| Fast | standard batch | Explicit urgent request only | Faster, higher cost |
| Canary | first 120 seconds | Validate media/language before full work | Required before chunks |

The UI lets the operator choose the strategy before preflight. The selected
strategy is persisted on the batch and every child job, and the estimate is
calculated with the matching Chirp rate. Switching a submitted job to another
mode is prohibited; an operator must first clear any retained operation and
duplicate-billing risk before creating a replacement.

## 2. Operational health command

Run the read-only health report with:

```bash
python -m app.operations.production_health
python -m app.operations.production_health --json
```

Exit codes:

- `0`: healthy
- `1`: warning
- `2`: critical

The report checks:

- missing or unreadable SQLite state;
- failed jobs;
- Dynamic Batch jobs waiting beyond 18/23/24-hour thresholds;
- pending Drive delivery retries;
- invalid alert threshold configuration.

It never calls Chirp, Gemini, Drive, or Billing and never resubmits paid work.
The JSON form is suitable for an authenticated dashboard, systemd timer, cron,
Uptime Kuma push wrapper, Telegram notification worker, or another monitoring
system.

Use `--output` when a persistent JSON report is required. The command writes
the file atomically before returning its health exit code, so warning and
critical results replace the previous report instead of leaving stale healthy
data.

Suggested schedule:

```cron
*/15 * * * * cd /opt/course-transcript && docker compose exec -T pipeline-worker \
  python -m app.operations.production_health --json \
  --output /app/data/production-health.json >/dev/null
```

The non-zero exit code can still create an alert. Do not wrap the write in
`command && mv`, because warning and critical statuses intentionally return
`1` and `2`. Do not let the health command perform automatic retries.

## 3. Cost reporting contract

Every approved job should expose these values separately:

1. Chirp recognition estimate;
2. Gemini correction estimate;
3. storage/transfer allowance;
4. reserved maximum;
5. actual recorded application cost;
6. Cloud Billing reconciled cost, when enabled;
7. estimate-to-actual variance.

Application estimates are safety gates, not invoices. Production acceptance
requires a Cloud Billing export check proving that economical jobs are charged
under the expected Dynamic Batch recognition SKU. Billing reconciliation must
remain read-only and use a dedicated service account.

Recommended acceptance tolerance:

- application estimate versus metered provider usage: within 10%;
- application usage versus Cloud Billing export: explain all material variance;
- no usage record may be duplicated after worker restart or Drive retry.

## 4. Dynamic Batch delay policy

Default thresholds:

- 18 hours: warning;
- 23 hours: critical, near operational limit;
- 24 hours: SLA/operational breach requiring manual review.

At or above 24 hours:

- do not automatically submit another Chirp operation;
- inspect saved operation names and chunk manifests;
- inspect provider status and billing evidence;
- record the operator decision in the job event history;
- only choose a Fast-mode replacement after duplicate billing risk is cleared.

## 5. Quality-control priorities

Human review should focus on high-risk windows rather than replaying an entire
course. Prioritize:

- every chunk join boundary;
- Gemini content-guard fallbacks;
- unresolved glossary candidates;
- unusually short or long subtitle cues;
- dense terminology sections;
- low-information or silent spans;
- repaired/re-transcribed chunks.

A single-chunk re-transcription must preserve the old operation evidence, charge
only the selected source window, rebuild downstream artifacts deterministically,
and never overwrite a newer human-edited subtitle revision.

## 6. Deployment acceptance gates

### Non-paid gate

- build Linux ARM64 images on the Oracle VPS;
- verify API, pipeline worker, and delivery worker share the same `/app/data`;
- import production entrypoints;
- run unit/integration tests;
- run Compose validation;
- verify restart persistence with fake providers;
- run `python -m app.operations.production_health`;
- verify database and complete `data/jobs/` backup/restore.

### Paid short-file gate

Requires explicit approval:

- one 5-10 minute file;
- verify 120-second canary;
- verify Dynamic Batch strategy is persisted;
- verify raw operation and usage evidence;
- verify Gemini correction evidence;
- verify selected outputs and QA.

### Paid long-file gate

Requires explicit approval:

- one file longer than 60 minutes;
- restart worker while Dynamic Batch is pending;
- prove recovery without duplicate submission;
- prove successful chunks are never resent;
- prove Drive delivery failure retries only local artifacts;
- reconcile estimated and actual provider cost.

### Disaster-recovery gate

- stop services;
- restore SQLite plus the complete jobs tree into an isolated directory;
- start with fake providers or provider calls disabled;
- verify pending operations, completed evidence, subtitle revisions, and Drive
  delivery state remain coherent.

## 7. Follow-up implementation sequence

1. Merge production health and alert thresholds.
2. Add an authenticated admin health endpoint that only returns the generated
   report; do not duplicate the scanning logic in the API.
3. Canary remains an internal validation step; Economical/Fast are selectable
   before preflight and the split cost estimate is shown after inspection.
4. Connect Cloud Billing read-only reconciliation and verify the actual SKU.
5. Add optional Telegram notifications consuming health JSON.
6. Complete single-chunk re-transcription with revision and cost isolation.
7. Add high-risk QA sampling to the subtitle editor.

Do not combine all follow-up work into one high-risk release. Each step should
retain CI evidence and pass the non-paid VPS gate before any real provider test.
