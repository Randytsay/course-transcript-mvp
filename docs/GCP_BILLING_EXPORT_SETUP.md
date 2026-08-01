# GCP Billing Export Setup

**Note:** This file is a manual procedural guide. Codex AI agents and CI/CD automation WILL NOT automatically mutate or configure your cloud accounts.

## Manual Steps

1. **Enable Billing Export:**
   - In Google Cloud Console, navigate to **Billing > Billing Export**.
   - Enable **Standard usage cost export** and select a BigQuery Dataset.

2. **Verify Target BigQuery Table:**
   - Ensure you see a table named like `gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX` populated in the dataset.

3. **Create a Dedicated Service Account:**
   - Go to IAM & Admin > Service Accounts.
   - Create a specific identity (e.g., `course-transcript-billing-sync`).
   - Grant **BigQuery Data Viewer** (on the dataset) and **BigQuery Job User** (on the project).

4. **Acquire & Secure JSON Key:**
   - Export the JSON Key and save it strictly to the Oracle VPS at:
     `/opt/course-transcript/secrets/billing-sa.json`
   - NEVER commit this key into GitHub.

5. **Configure VPS Environment Variables:**
   - Update `/opt/course-transcript/.env`:
     ```env
     BILLING_ENABLED=true
     BILLING_BIGQUERY_PROJECT=your-bigquery-host-project
     BILLING_BIGQUERY_DATASET=your_dataset_name
     BILLING_BIGQUERY_TABLE=gcp_billing_export_v1_XXXXXX
     BILLING_TARGET_PROJECT_ID=course-transcript-mvp
     BILLING_TRACKING_START_DATE=2026-07-01
     ```

6. **Activate the Worker:**
   - Run `docker compose --profile web up -d billing-worker` to start the synchronous crawler. Check `/api/v1/billing/summary` for data propagation.