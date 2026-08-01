"""Safe Cloud Billing standard-export query construction."""
from __future__ import annotations

from app.billing.config import BillingConfig


_QUERY_TEMPLATE = """
WITH billing_rows AS (
  SELECT
    project.id AS project_id,
    cost,
    currency,
    currency_conversion_rate,
    usage_start_time,
    (
      SELECT COALESCE(SUM(credit.amount), 0)
      FROM UNNEST(credits) AS credit
    ) AS all_credits_local,
    (
      SELECT COALESCE(SUM(SAFE_DIVIDE(credit.amount, currency_conversion_rate)), 0)
      FROM UNNEST(credits) AS credit
    ) AS all_credits_usd,
    (
      SELECT COALESCE(SUM(credit.amount), 0)
      FROM UNNEST(credits) AS credit
      WHERE credit.type = 'PROMOTION'
    ) AS promotion_credits_local,
    (
      SELECT COALESCE(SUM(SAFE_DIVIDE(credit.amount, currency_conversion_rate)), 0)
      FROM UNNEST(credits) AS credit
      WHERE credit.type = 'PROMOTION'
    ) AS promotion_credits_usd,
    (
      SELECT COALESCE(SUM(SAFE_DIVIDE(credit.amount, currency_conversion_rate)), 0)
      FROM UNNEST(credits) AS credit
      WHERE credit.type = 'PROMOTION'
        AND LOWER(CONCAT(
          COALESCE(credit.id, ''), ' ',
          COALESCE(credit.name, ''), ' ',
          COALESCE(credit.full_name, '')
        )) LIKE LOWER(@promotion_pattern)
    ) AS free_trial_credits_usd
  FROM {qualified_table}
  WHERE usage_start_time >= TIMESTAMP(@start_date)
),
metadata AS (
  SELECT
    ARRAY_AGG(DISTINCT currency IGNORE NULLS) AS currencies,
    COUNTIF(currency_conversion_rate IS NULL OR currency_conversion_rate = 0) AS missing_conversion_rows,
    MAX(usage_start_time) AS latest_usage_start_time
  FROM billing_rows
),
project_summary AS (
  SELECT
    COALESCE(SUM(cost), 0) AS project_gross_cost_local,
    COALESCE(SUM(SAFE_DIVIDE(cost, currency_conversion_rate)), 0) AS project_gross_cost_usd,
    COALESCE(SUM(all_credits_local), 0) AS project_credits_local,
    COALESCE(SUM(all_credits_usd), 0) AS project_credits_usd
  FROM billing_rows
  WHERE project_id = @target_project
),
account_summary AS (
  SELECT
    COALESCE(SUM(promotion_credits_local), 0) AS account_promotion_credits_local,
    COALESCE(SUM(promotion_credits_usd), 0) AS account_promotion_credits_usd,
    COALESCE(SUM(free_trial_credits_usd), 0) AS free_trial_credits_usd
  FROM billing_rows
)
SELECT
  IF(ARRAY_LENGTH(metadata.currencies) = 1, metadata.currencies[OFFSET(0)], NULL) AS billing_currency,
  ARRAY_LENGTH(metadata.currencies) AS currency_count,
  metadata.missing_conversion_rows,
  metadata.latest_usage_start_time,
  project_summary.project_gross_cost_local,
  project_summary.project_gross_cost_usd,
  project_summary.project_credits_local,
  project_summary.project_credits_usd,
  project_summary.project_gross_cost_local + project_summary.project_credits_local AS project_net_cost_local,
  project_summary.project_gross_cost_usd + project_summary.project_credits_usd AS project_net_cost_usd,
  account_summary.account_promotion_credits_local,
  account_summary.account_promotion_credits_usd,
  account_summary.free_trial_credits_usd
FROM metadata, project_summary, account_summary
"""


def build_billing_summary_query(config: BillingConfig) -> str:
    """Insert only a validated table identifier; values remain query parameters."""
    return _QUERY_TEMPLATE.format(qualified_table=config.qualified_table)
