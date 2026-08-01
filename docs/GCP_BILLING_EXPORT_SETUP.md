# GCP Cloud Billing BigQuery Export Setup

This is a manual checklist. The application, Codex, and GitHub Actions must not
create billing resources, change IAM, enable exports, or display a service
account key.

## Before starting

- Confirm the Cloud Billing account attached to `course-transcript-mvp`.
- Decide which Google Cloud project will host the billing-export dataset.
- Keep the normal transcription service account separate from the billing
  reader.
- Billing data is delayed. This feature is not an official real-time balance.

## 1. Enable the standard usage-cost export

In Google Cloud Console:

1. Open **Billing**.
2. Select the billing account.
3. Open **Billing export**.
4. Under **Standard usage cost**, choose **Edit settings**.
5. Select or create a BigQuery dataset.
6. Save the export configuration.

Do not enable detailed export unless another use case requires it. This
application reads only the standard export schema.

## 2. Wait for and verify the export table

In BigQuery, confirm a table similar to:

```text
gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX
```

Record these three values without including backticks:

```text
BILLING_BIGQUERY_PROJECT
BILLING_BIGQUERY_DATASET
BILLING_BIGQUERY_TABLE
```

The project, dataset, and table identifiers are validated by the application.
Do not pass a full SQL expression.

## 3. Create a dedicated billing reader

Create a service account such as:

```text
course-transcript-billing-reader
```

Grant only the permissions needed to run read-only queries:

- **BigQuery Job User** on the query/billing-export project;
- **BigQuery Data Viewer** on the selected billing-export dataset.

Do not grant:

- Billing Account Administrator;
- Project Owner or Editor;
- IAM administrator roles;
- Storage Object Admin;
- Vertex AI User;
- Speech-to-Text roles;
- Google Drive write access.

The application only needs to read the export table and run a bounded query.

## 4. Place the credential on the VPS

Store the dedicated JSON key at:

```text
/opt/course-transcript/secrets/billing-sa.json
```

Recommended ownership and mode:

```bash
sudo chown root:root /opt/course-transcript/secrets/billing-sa.json
sudo chmod 600 /opt/course-transcript/secrets/billing-sa.json
```

Never print, paste, commit, upload, or copy the JSON contents into a prompt.
Do not reuse the transcription worker credential for this optional service.

## 5. Configure `/opt/course-transcript/.env`

```env
BILLING_ENABLED=true
BILLING_BIGQUERY_PROJECT=your-query-project
BILLING_BIGQUERY_DATASET=your_dataset
BILLING_BIGQUERY_TABLE=gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX
BILLING_TARGET_PROJECT_ID=course-transcript-mvp
BILLING_TRACKING_START_DATE=2026-07-01
BILLING_INITIAL_FREE_TRIAL_CREDIT_USD=300
BILLING_PROMOTION_NAME_PATTERN=Free trial
BILLING_SYNC_INTERVAL_SECONDS=900
BILLING_SNAPSHOT_STALE_SECONDS=3600
BILLING_MAXIMUM_BYTES_BILLED=1000000000
BILLING_CREDENTIALS_HOST_PATH=/opt/course-transcript/secrets/billing-sa.json
```

`BILLING_PROMOTION_NAME_PATTERN` must match the free-trial promotion name shown
in your export. If it does not match, project and account costs can still
appear, but estimated free-trial remaining credit may show unavailable or the
configured initial amount.

## 6. Validate configuration without starting paid transcription

```bash
cd /opt/course-transcript
docker compose --profile billing config --quiet
sudo docker compose --profile billing build billing-worker
```

These commands do not call Chirp or Gemini.

## 7. Start only the optional billing worker

```bash
sudo docker compose --profile billing up -d billing-worker
sudo docker compose --profile billing ps
```

The ordinary web profile does not depend on this worker.

## 8. Verify the snapshot

Inspect metadata only; do not expose credentials:

```bash
sudo docker compose logs --tail=100 billing-worker
ls -l /opt/course-transcript/data/billing/billing_snapshot.json
```

Through the authenticated application, verify:

```text
GET /api/v1/billing/summary
```

Expected status progression:

```text
pending -> ok
```

A stale status means the previous successful snapshot is retained. An error
status means no successful snapshot is available yet.

## 9. Compare with Google Cloud Console

Verify the same date range and scope:

- project gross usage: `course-transcript-mvp` only;
- project net usage: project cost plus project credits;
- account promotional credits: all projects in the billing account export;
- free-trial promotion use: only the matched promotion;
- official remaining credit: Billing Overview.

Small differences can occur because the export is delayed and the application
uses the latest exported rows.

## Disable or roll back

Stop the optional worker:

```bash
sudo docker compose --profile billing stop billing-worker
```

Set:

```env
BILLING_ENABLED=false
```

The transcription website and paid pipeline remain functional. Do not delete
the BigQuery export or billing account merely to disable the dashboard cards.
