from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from app.billing.config import BillingConfig, BillingConfigError
from app.billing.query import build_billing_summary_query
from app.billing.snapshot import atomic_write_json, snapshot_for_api
from app.billing.worker import sync_billing


class BillingConfigTests(unittest.TestCase):
    def test_rejects_unsafe_table_identifier(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BILLING_ENABLED": "true",
                "BILLING_BIGQUERY_PROJECT": "course-transcript-mvp",
                "BILLING_BIGQUERY_DATASET": "billing_export",
                "BILLING_BIGQUERY_TABLE": "table`; DROP TABLE x; --",
                "BILLING_TARGET_PROJECT_ID": "course-transcript-mvp",
            },
            clear=False,
        ):
            with self.assertRaises(BillingConfigError):
                BillingConfig.from_env()

    def test_query_separates_project_and_account_scope(self) -> None:
        config = BillingConfig(
            enabled=True,
            bigquery_project="course-transcript-mvp",
            dataset="billing_export",
            table="gcp_billing_export_v1_ABC123",
            target_project_id="course-transcript-mvp",
            tracking_start_date=date(2026, 7, 1),
            initial_free_trial_credit_usd=Decimal("300"),
            promotion_name_pattern="Free trial",
            sync_interval_seconds=900,
            snapshot_stale_seconds=3600,
            maximum_bytes_billed=1_000_000,
            data_dir=Path("/tmp"),
            credentials_path=None,
        )
        query = build_billing_summary_query(config)
        self.assertEqual(query.count("project_id = @target_project"), 1)
        self.assertIn("account_summary AS", query)
        self.assertIn("credit.type = 'PROMOTION'", query)
        self.assertNotIn("DROP TABLE", query)


class BillingSnapshotTests(unittest.TestCase):
    def test_stale_snapshot_is_returned_instead_of_500(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "billing_snapshot.json"
            generated = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            atomic_write_json(
                path,
                {
                    "status": "ok",
                    "snapshotGeneratedAt": generated,
                    "projectGrossCost": "10.00",
                },
            )
            result = snapshot_for_api(path, enabled=True, stale_seconds=3600)
            self.assertEqual(result["status"], "stale")
            self.assertEqual(result["projectGrossCost"], "10.00")
            self.assertGreater(result["dataAgeSeconds"], 3600)

    def test_disabled_mode_does_not_require_snapshot(self) -> None:
        result = snapshot_for_api(
            Path("/does/not/exist.json"), enabled=False, stale_seconds=3600
        )
        self.assertEqual(result["status"], "disabled")


class BillingWorkerTests(unittest.TestCase):
    def test_worker_uses_account_promotions_and_real_currency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = BillingConfig(
                enabled=True,
                bigquery_project="course-transcript-mvp",
                dataset="billing_export",
                table="gcp_billing_export_v1_ABC123",
                target_project_id="course-transcript-mvp",
                tracking_start_date=date(2026, 7, 1),
                initial_free_trial_credit_usd=Decimal("300"),
                promotion_name_pattern="Free trial",
                sync_interval_seconds=900,
                snapshot_stale_seconds=3600,
                maximum_bytes_billed=1_000_000,
                data_dir=Path(temporary),
                credentials_path=None,
            )
            row = {
                "billing_currency": "TWD",
                "currency_count": 1,
                "missing_conversion_rows": 0,
                "latest_usage_start_time": datetime(2026, 8, 1, tzinfo=UTC),
                "project_gross_cost_local": Decimal("685.85"),
                "project_gross_cost_usd": Decimal("21.10"),
                "project_credits_local": Decimal("-685.85"),
                "project_credits_usd": Decimal("-21.10"),
                "project_net_cost_local": Decimal("0"),
                "project_net_cost_usd": Decimal("0"),
                "account_promotion_credits_local": Decimal("-900.00"),
                "account_promotion_credits_usd": Decimal("-27.70"),
                "free_trial_credits_usd": Decimal("-21.10"),
            }
            query_job = Mock()
            query_job.result.return_value = [row]
            client = Mock()
            client.query.return_value = query_job

            with patch("google.cloud.bigquery.Client", return_value=client):
                result = sync_billing(config)

            self.assertEqual(result["billingCurrency"], "TWD")
            self.assertEqual(result["projectGrossCost"], "685.85")
            self.assertEqual(result["accountPromotionCreditsUsed"], "900.00")
            self.assertEqual(
                result["estimatedRemainingFreeTrialCreditUsd"], "278.90"
            )
            self.assertEqual(
                result["lastBillingDataAt"], "2026-08-01T00:00:00+00:00"
            )
            client.query.assert_called_once()

    def test_missing_conversion_rate_hides_usd_and_remaining_credit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = BillingConfig(
                enabled=True,
                bigquery_project="course-transcript-mvp",
                dataset="billing_export",
                table="gcp_billing_export_v1_ABC123",
                target_project_id="course-transcript-mvp",
                tracking_start_date=date(2026, 7, 1),
                initial_free_trial_credit_usd=Decimal("300"),
                promotion_name_pattern="Free trial",
                sync_interval_seconds=900,
                snapshot_stale_seconds=3600,
                maximum_bytes_billed=1_000_000,
                data_dir=Path(temporary),
                credentials_path=None,
            )
            row = {
                "billing_currency": "TWD",
                "currency_count": 1,
                "missing_conversion_rows": 2,
                "latest_usage_start_time": None,
                "project_gross_cost_local": 10,
                "project_credits_local": -10,
                "project_net_cost_local": 0,
                "account_promotion_credits_local": -10,
            }
            query_job = Mock()
            query_job.result.return_value = [row]
            client = Mock()
            client.query.return_value = query_job
            with patch("google.cloud.bigquery.Client", return_value=client):
                result = sync_billing(config)
            self.assertIsNone(result["projectGrossCostUsd"])
            self.assertIsNone(result["estimatedRemainingFreeTrialCreditUsd"])
            self.assertFalse(result["conversionComplete"])


if __name__ == "__main__":
    unittest.main()
