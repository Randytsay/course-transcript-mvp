"""Dated, conservative USD reservations; usage-derived cost is not an invoice.

Standard global Gemini 3.8 introductory prices and Speech V2 dynamic batch
list prices were independently checked on 2026-10-14. No free-tier or savings
discounts assumed. Unknown usage and expired pricing fail closed.
"""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING
import math

from app.providers.gemini_primary_audio import plan_windows

PRICE_VERSION = 'google-list-global-20261014'
INPUT_TOKEN_CAP = 200000
OUTPUT_TOKEN_CAP = 65536
PRICE_SOURCES = [
    'https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing',
    'https://cloud.google.com/speech-to-text/pricing',
]


def assert_current(today=None):
    # Release verification date is recorded in the owner's Asia/Taipei date.
    # VPS containers use UTC; their date can lag eight hours at deployment.
    current = today or datetime.now(timezone(timedelta(hours=8))).date()
    if not date(2026, 10, 14) <= current <= date(2026, 12, 31):
        raise ValueError('pricing requires independent renewal')


def cents(value):
    return int((Decimal(value) * 100).to_integral_value(rounding=ROUND_CEILING))


def chirp_cents(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('verified billable duration required')
    return cents(Decimal(math.ceil(seconds)) * Decimal('0.003') / 60)


def standard_chirp_cents(seconds):
    """Speech-to-Text V2 Standard Batch (non-Dynamic) conservative ceiling.

    Standard repair requests must not reuse the discounted Dynamic Batch
    estimate. Pricing is versioned by PRICE_VERSION; provider invoice and
    GCS charges are still separate reconciliation obligations.
    """
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('verified Standard Chirp billable duration required')
    return cents(Decimal(math.ceil(seconds)) * Decimal('0.016') / 60)


def chirp_operation_cents(row, billed_seconds):
    """Use the recorded submission strategy, never a discounted rate by default."""
    if not isinstance(row, dict):
        raise ValueError('verified Chirp operation metadata required')
    purpose = row.get('purpose')
    if purpose == 'REPAIR':
        return standard_chirp_cents(billed_seconds)
    if purpose == 'BASE':
        return chirp_cents(billed_seconds)
    raise ValueError('unknown Chirp operation price category')


def quote_duration(seconds, *, today=None):
    assert_current(today)
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 43200:
        raise ValueError('bounded measured audio duration required')
    primary = plan_windows(seconds, max_seconds=2700, overlap_seconds=10)
    chirp = plan_windows(seconds, max_seconds=900, overlap_seconds=10)
    # Each real request must independently count tokens and enforce these caps.
    per_primary = cents((Decimal(INPUT_TOKEN_CAP) * Decimal('0.75') +
                         Decimal(OUTPUT_TOKEN_CAP) * Decimal('3.75')) / 1000000)
    estimates = dict(gemini_primary=per_primary * len(primary),
                     chirp_independent=sum(chirp_cents(w['end_seconds']-w['start_seconds']) for w in chirp),
                     compare=0, third_optional=0, adjudication=0, golden_qa=0)
    return {'schema':'golden-v2-trusted-reservation-v1', 'price_version':PRICE_VERSION,
            'valid_through':'2026-12-31', 'price_sources':PRICE_SOURCES,
            'duration_seconds':seconds, 'mode':'FLASH_CHIRP',
            'primary_window_count':len(primary), 'chirp_window_count':len(chirp),
            'input_token_cap_per_request':INPUT_TOKEN_CAP,
            'output_token_cap_per_request':OUTPUT_TOKEN_CAP,
            'estimated_stage_cents':estimates, 'estimated_cents':sum(estimates.values()),
            'quote_kind':'CONSERVATIVE_PROVIDER_RESERVATION',
            'invoice_verified':False, 'storage_and_network_included':False,
            'provider_calls_started':False, 'drive_mutation_started':False}


def gemini_usage_cents(records):
    total = Decimal(0)
    for record in records:
        usage = record.get('usage_metadata', {})
        values = [usage.get('prompt_token_count'), usage.get('candidates_token_count'),
                  usage.get('thoughts_token_count', 0)]
        if any(type(n) is not int or n < 0 for n in values) or values[0] == 0:
            raise ValueError('complete provider token usage required')
        cached = usage.get('cached_content_token_count',0)
        if type(cached) is not int or not 0 <= cached <= values[0]:
            raise ValueError('invalid provider cached token usage')
        total += (Decimal(values[0]-cached)*Decimal('0.75') + Decimal(cached)*Decimal('0.075') +
                  Decimal(values[1]+values[2])*Decimal('3.75')) / 1000000
    return cents(total)
