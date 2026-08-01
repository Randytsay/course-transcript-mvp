"""Billing sync worker."""
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from google.cloud import bigquery
from app.billing.query import BILLING_SUMMARY_QUERY

DATA_DIR = Path("/app/data/billing")
SNAPSHOT_FILE = DATA_DIR / "billing_snapshot.json"

def atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)

def sync_billing():
    enabled = os.environ.get("BILLING_ENABLED", "false").lower() == "true"
    if not enabled:
        atomic(SNAPSHOT_FILE, {"status": "disabled", "warning": "尚未設定Cloud Billing BigQuery匯出", "lastBillingDataAt": None, "snapshotGeneratedAt": datetime.now(UTC).isoformat()})
        return

    bq_project = os.environ.get("BILLING_BIGQUERY_PROJECT")
    dataset = os.environ.get("BILLING_BIGQUERY_DATASET")
    table = os.environ.get("BILLING_BIGQUERY_TABLE")
    target_project = os.environ.get("BILLING_TARGET_PROJECT_ID", "course-transcript-mvp")
    start_date = os.environ.get("BILLING_TRACKING_START_DATE", "2026-07-01")
    promo_pattern = f"%{os.environ.get('BILLING_PROMOTION_NAME_PATTERN', 'Free trial')}%"
    initial_free_trial = Decimal(os.environ.get("BILLING_INITIAL_FREE_TRIAL_CREDIT_USD", "300.00"))

    if not all([bq_project, dataset, table]):
        atomic(SNAPSHOT_FILE, {"status": "error", "warning": "Missing BigQuery configuration", "lastBillingDataAt": None, "snapshotGeneratedAt": datetime.now(UTC).isoformat()})
        return

    try:
        client = bigquery.Client(project=bq_project)
        query = BILLING_SUMMARY_QUERY.format(project_id=bq_project, dataset=dataset, table=table)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("target_project", "STRING", target_project),
                bigquery.ScalarQueryParameter("start_date", "STRING", start_date),
                bigquery.ScalarQueryParameter("promo_pattern", "STRING", promo_pattern),
            ],
            maximum_bytes_billed=int(os.environ.get("BILLING_MAXIMUM_BYTES_BILLED", "1000000000")) # 1GB
        )
        
        query_job = client.query(query, job_config=job_config)
        results = list(query_job.result())
        
        if not results:
            raise ValueError("No results from BigQuery")
            
        row = results[0]
        gross_cost_local = Decimal(str(row.gross_cost_local or 0))
        gross_cost_usd = Decimal(str(row.gross_cost_usd or 0)) if row.gross_cost_usd is not None else None
        total_credits_local = Decimal(str(row.total_credits_local or 0))
        
        promo_credits_local = Decimal(str(row.promo_credits_local or 0))
        promo_credits_usd = Decimal(str(row.promo_credits_usd or 0)) if row.promo_credits_usd is not None else None
        free_trial_used_usd = Decimal(str(row.free_trial_usd or 0)) if row.free_trial_usd is not None else None
        
        net_cost_local = gross_cost_local + total_credits_local
        
        remaining_free_trial = None
        if free_trial_used_usd is not None:
            remaining_free_trial = max(Decimal("0.00"), initial_free_trial - abs(free_trial_used_usd))
            
        data = {
            "status": "ok",
            "source": "bigquery_standard_billing_export",
            "targetProjectId": target_project,
            "billingCurrency": "TWD", # Ideally inferred from BQ but hardcoded for MVP
            "projectGrossCost": str(gross_cost_local.quantize(Decimal("0.01"))),
            "projectCredits": str(total_credits_local.quantize(Decimal("0.01"))),
            "projectNetCost": str(net_cost_local.quantize(Decimal("0.01"))),
            "projectGrossCostUsd": str(gross_cost_usd.quantize(Decimal("0.01"))) if gross_cost_usd is not None else None,
            "accountPromotionCreditsUsed": str(abs(promo_credits_local).quantize(Decimal("0.01"))),
            "accountPromotionCreditsUsedUsd": str(abs(promo_credits_usd).quantize(Decimal("0.01"))) if promo_credits_usd is not None else None,
            "initialFreeTrialCreditUsd": str(initial_free_trial.quantize(Decimal("0.01"))),
            "estimatedRemainingFreeTrialCreditUsd": str(remaining_free_trial.quantize(Decimal("0.01"))) if remaining_free_trial is not None else None,
            "coverageStart": start_date,
            "coverageEnd": datetime.now(UTC).strftime("%Y-%m-%d"),
            "lastBillingDataAt": datetime.now(UTC).isoformat(),
            "snapshotGeneratedAt": datetime.now(UTC).isoformat(),
            "isEstimatedRemainingCredit": True,
            "warning": "Cloud Billing資料可能延遲；官方剩餘抵免額請以Billing Overview為準。"
        }
        atomic(SNAPSHOT_FILE, data)
    except Exception as e:
        # Keep old snapshot if it exists, but add error
        if SNAPSHOT_FILE.exists():
            try:
                old = json.loads(SNAPSHOT_FILE.read_text())
                old["status"] = "stale"
                old["lastError"] = str(e)
                old["lastAttemptAt"] = datetime.now(UTC).isoformat()
                atomic(SNAPSHOT_FILE, old)
            except Exception:
                pass
        else:
            atomic(SNAPSHOT_FILE, {"status": "error", "warning": str(e), "lastBillingDataAt": None, "snapshotGeneratedAt": datetime.now(UTC).isoformat()})

def main():
    sync_interval = int(os.environ.get("BILLING_SYNC_INTERVAL_SECONDS", "900"))
    while True:
        sync_billing()
        time.sleep(sync_interval)

if __name__ == "__main__":
    main()