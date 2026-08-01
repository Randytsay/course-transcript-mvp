"""BigQuery billing queries."""

BILLING_SUMMARY_QUERY = """
WITH raw_billing AS (
    SELECT
        project.id AS project_id,
        cost,
        currency,
        currency_conversion_rate,
        usage_start_time,
        credits
    FROM `{project_id}.{dataset}.{table}`
    WHERE project.id = @target_project
      AND usage_start_time >= TIMESTAMP(@start_date)
),
cost_summary AS (
    SELECT
        SUM(cost) AS gross_cost_local,
        SUM(cost / NULLIF(currency_conversion_rate, 0)) AS gross_cost_usd
    FROM raw_billing
),
credits_summary AS (
    SELECT
        SUM((SELECT COALESCE(SUM(c.amount), 0) FROM UNNEST(credits) c)) AS total_credits_local,
        SUM((SELECT COALESCE(SUM(c.amount / NULLIF(currency_conversion_rate, 0)), 0) FROM UNNEST(credits) c)) AS total_credits_usd
    FROM raw_billing
),
promo_summary AS (
    SELECT
        SUM((SELECT COALESCE(SUM(c.amount), 0) FROM UNNEST(credits) c WHERE c.type = 'PROMOTION')) AS promo_credits_local,
        SUM((SELECT COALESCE(SUM(c.amount / NULLIF(currency_conversion_rate, 0)), 0) FROM UNNEST(credits) c WHERE c.type = 'PROMOTION')) AS promo_credits_usd,
        SUM((SELECT COALESCE(SUM(c.amount / NULLIF(currency_conversion_rate, 0)), 0) FROM UNNEST(credits) c WHERE c.type = 'PROMOTION' AND (c.name LIKE @promo_pattern OR c.full_name LIKE @promo_pattern))) AS free_trial_usd
    FROM raw_billing
)
SELECT
    cost_summary.gross_cost_local,
    cost_summary.gross_cost_usd,
    credits_summary.total_credits_local,
    credits_summary.total_credits_usd,
    promo_summary.promo_credits_local,
    promo_summary.promo_credits_usd,
    promo_summary.free_trial_usd
FROM cost_summary, credits_summary, promo_summary
"""