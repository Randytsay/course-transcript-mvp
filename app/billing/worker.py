"""Optional Cloud Billing standard-export synchronization worker."""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.billing.config import BillingConfig, BillingConfigError
from app.billing.query import build_billing_summary_query
from app.billing.snapshot import (
    atomic_write_json,
    iso_now,
    preserve_previous_failure,
    write_disabled,
)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or 0))


def _money(value: Decimal | None) -> str | None:
    return str(value.quantize(Decimal("0.01"))) if value is not None else None


def _row_value(row: Any, name: str, default: object = None) -> object:
    try:
        return row[name]
    except (KeyError, TypeError):
        return getattr(row, name, default)


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat()
    return str(value)


def sync_billing(config: BillingConfig | None = None) -> dict[str, Any]:
    try:
        config = config or BillingConfig.from_env()
    except BillingConfigError as exc:
        fallback = BillingConfig.from_env.__func__  # type: ignore[attr-defined]
        del fallback
        data_dir = os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data")
        from pathlib import Path

        return preserve_previous_failure(
            Path(data_dir) / "billing" / "billing_snapshot.json", exc
        )

    if not config.enabled:
        return write_disabled(config.snapshot_path)

    try:
        # Lazy import keeps the transcription web profile functional when billing
        # is disabled and makes the optional dependency boundary explicit.
        from google.cloud import bigquery

        client = bigquery.Client(project=config.bigquery_project)
        query = build_billing_summary_query(config)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "target_project", "STRING", config.target_project_id
                ),
                bigquery.ScalarQueryParameter(
                    "start_date", "DATE", config.tracking_start_date
                ),
                bigquery.ScalarQueryParameter(
                    "promotion_pattern",
                    "STRING",
                    f"%{config.promotion_name_pattern.lower()}%",
                ),
            ],
            maximum_bytes_billed=config.maximum_bytes_billed,
            use_query_cache=True,
        )
        rows = list(client.query(query, job_config=job_config).result())
        if len(rows) != 1:
            raise RuntimeError("billing query returned an unexpected row count")
        row = rows[0]

        currency_count = int(_row_value(row, "currency_count", 0) or 0)
        billing_currency = _row_value(row, "billing_currency")
        single_currency = currency_count == 1 and bool(billing_currency)
        conversion_complete = (
            int(_row_value(row, "missing_conversion_rows", 0) or 0) == 0
        )

        project_gross_local = _decimal(
            _row_value(row, "project_gross_cost_local", 0)
        )
        project_credits_local = _decimal(
            _row_value(row, "project_credits_local", 0)
        )
        project_net_local = _decimal(
            _row_value(row, "project_net_cost_local", 0)
        )
        account_promo_local = abs(
            _decimal(_row_value(row, "account_promotion_credits_local", 0))
        )

        project_gross_usd = (
            _decimal(_row_value(row, "project_gross_cost_usd", 0))
            if conversion_complete
            else None
        )
        project_credits_usd = (
            _decimal(_row_value(row, "project_credits_usd", 0))
            if conversion_complete
            else None
        )
        project_net_usd = (
            _decimal(_row_value(row, "project_net_cost_usd", 0))
            if conversion_complete
            else None
        )
        account_promo_usd = (
            abs(_decimal(_row_value(row, "account_promotion_credits_usd", 0)))
            if conversion_complete
            else None
        )
        free_trial_used_usd = (
            abs(_decimal(_row_value(row, "free_trial_credits_usd", 0)))
            if conversion_complete
            else None
        )
        remaining_credit = (
            max(
                Decimal("0"),
                config.initial_free_trial_credit_usd - free_trial_used_usd,
            )
            if free_trial_used_usd is not None
            else None
        )
        latest_usage = _timestamp(
            _row_value(row, "latest_usage_start_time")
        )
        generated_at = iso_now()

        payload: dict[str, Any] = {
            "status": "ok",
            "source": "bigquery_standard_billing_export",
            "targetProjectId": config.target_project_id,
            "billingCurrency": str(billing_currency) if single_currency else None,
            "currencyCount": currency_count,
            "projectGrossCost": _money(project_gross_local)
            if single_currency
            else None,
            "projectCredits": _money(project_credits_local)
            if single_currency
            else None,
            "projectNetCost": _money(project_net_local)
            if single_currency
            else None,
            "accountPromotionCreditsUsed": _money(account_promo_local)
            if single_currency
            else None,
            "projectGrossCostUsd": _money(project_gross_usd),
            "projectCreditsUsd": _money(project_credits_usd),
            "projectNetCostUsd": _money(project_net_usd),
            "accountPromotionCreditsUsedUsd": _money(account_promo_usd),
            "freeTrialPromotionCreditsUsedUsd": _money(free_trial_used_usd),
            "initialFreeTrialCreditUsd": _money(
                config.initial_free_trial_credit_usd
            ),
            "estimatedRemainingFreeTrialCreditUsd": _money(remaining_credit),
            "coverageStart": config.tracking_start_date.isoformat(),
            "coverageEnd": latest_usage[:10] if latest_usage else None,
            "lastBillingDataAt": latest_usage,
            "snapshotGeneratedAt": generated_at,
            "lastAttemptAt": generated_at,
            "dataAgeSeconds": 0,
            "isEstimatedRemainingCredit": True,
            "conversionComplete": conversion_complete,
            "warning": (
                "Cloud Billing 資料可能延遲；官方剩餘抵免額請以 Billing Overview 為準。"
            ),
        }
        if not single_currency:
            payload["warning"] = (
                "帳務匯出包含多種或未知幣別，本幣金額暫不顯示；"
                "官方帳務請以 Billing Overview 為準。"
            )
        elif not conversion_complete:
            payload["warning"] = (
                "部分帳務列缺少匯率，USD 換算與剩餘抵免額暫不顯示；"
                "官方帳務請以 Billing Overview 為準。"
            )
        atomic_write_json(config.snapshot_path, payload)
        return payload
    except Exception as exc:
        return preserve_previous_failure(config.snapshot_path, exc)


def main() -> int:
    try:
        config = BillingConfig.from_env()
    except BillingConfigError as exc:
        data_dir = os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data")
        from pathlib import Path

        preserve_previous_failure(
            Path(data_dir) / "billing" / "billing_snapshot.json", exc
        )
        return 2

    while True:
        sync_billing(config)
        time.sleep(config.sync_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
