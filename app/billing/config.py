"""Validated configuration for the optional Cloud Billing reader."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_DATASET_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_TABLE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")


class BillingConfigError(ValueError):
    """Raised when optional billing configuration is unsafe or incomplete."""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise BillingConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise BillingConfigError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class BillingConfig:
    enabled: bool
    bigquery_project: str | None
    dataset: str | None
    table: str | None
    target_project_id: str
    tracking_start_date: date
    initial_free_trial_credit_usd: Decimal
    promotion_name_pattern: str
    sync_interval_seconds: int
    snapshot_stale_seconds: int
    maximum_bytes_billed: int
    data_dir: Path
    credentials_path: str | None

    @property
    def snapshot_path(self) -> Path:
        return self.data_dir / "billing" / "billing_snapshot.json"

    @property
    def qualified_table(self) -> str:
        if not self.enabled or not self.bigquery_project or not self.dataset or not self.table:
            raise BillingConfigError("billing export is not fully configured")
        return f"`{self.bigquery_project}.{self.dataset}.{self.table}`"

    @classmethod
    def from_env(cls) -> "BillingConfig":
        enabled = _truthy(os.environ.get("BILLING_ENABLED"))
        bigquery_project = (os.environ.get("BILLING_BIGQUERY_PROJECT") or "").strip() or None
        dataset = (os.environ.get("BILLING_BIGQUERY_DATASET") or "").strip() or None
        table = (os.environ.get("BILLING_BIGQUERY_TABLE") or "").strip() or None
        target_project_id = (
            os.environ.get("BILLING_TARGET_PROJECT_ID", "course-transcript-mvp").strip()
        )
        start_raw = os.environ.get("BILLING_TRACKING_START_DATE", "2026-07-01").strip()
        try:
            tracking_start_date = date.fromisoformat(start_raw)
        except ValueError as exc:
            raise BillingConfigError("BILLING_TRACKING_START_DATE must be YYYY-MM-DD") from exc
        try:
            initial_credit = Decimal(
                os.environ.get("BILLING_INITIAL_FREE_TRIAL_CREDIT_USD", "300").strip()
            )
        except InvalidOperation as exc:
            raise BillingConfigError(
                "BILLING_INITIAL_FREE_TRIAL_CREDIT_USD must be numeric"
            ) from exc
        if initial_credit < 0:
            raise BillingConfigError(
                "BILLING_INITIAL_FREE_TRIAL_CREDIT_USD cannot be negative"
            )
        promotion_pattern = os.environ.get(
            "BILLING_PROMOTION_NAME_PATTERN", "Free trial"
        ).strip()
        if not promotion_pattern or len(promotion_pattern) > 120:
            raise BillingConfigError(
                "BILLING_PROMOTION_NAME_PATTERN must contain 1 to 120 characters"
            )

        if not _PROJECT_ID.fullmatch(target_project_id):
            raise BillingConfigError("BILLING_TARGET_PROJECT_ID is invalid")
        if bigquery_project and not _PROJECT_ID.fullmatch(bigquery_project):
            raise BillingConfigError("BILLING_BIGQUERY_PROJECT is invalid")
        if dataset and not _DATASET_ID.fullmatch(dataset):
            raise BillingConfigError("BILLING_BIGQUERY_DATASET is invalid")
        if table and not _TABLE_ID.fullmatch(table):
            raise BillingConfigError("BILLING_BIGQUERY_TABLE is invalid")
        if enabled and not all((bigquery_project, dataset, table)):
            raise BillingConfigError(
                "BILLING_ENABLED requires project, dataset, and table"
            )

        return cls(
            enabled=enabled,
            bigquery_project=bigquery_project,
            dataset=dataset,
            table=table,
            target_project_id=target_project_id,
            tracking_start_date=tracking_start_date,
            initial_free_trial_credit_usd=initial_credit,
            promotion_name_pattern=promotion_pattern,
            sync_interval_seconds=_positive_int(
                "BILLING_SYNC_INTERVAL_SECONDS", 900
            ),
            snapshot_stale_seconds=_positive_int(
                "BILLING_SNAPSHOT_STALE_SECONDS", 3600
            ),
            maximum_bytes_billed=_positive_int(
                "BILLING_MAXIMUM_BYTES_BILLED", 1_000_000_000
            ),
            data_dir=Path(
                os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data")
            ),
            credentials_path=(
                os.environ.get("BILLING_GOOGLE_APPLICATION_CREDENTIALS")
                or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
                or None
            ),
        )
