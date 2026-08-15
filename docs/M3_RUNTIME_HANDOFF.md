# MiniMax M3 Runtime Handoff

## Scope already implemented on `agent/m3-routing-ux`

- UI supports a persisted correction preference: `GEMINI_FIRST` or `M3_FIRST`.
- `M3_FIRST` stays disabled in the UI while `MINIMAX_M3_ENABLED=false`.
- API persists the requested policy in `job_correction_policies` without changing the core `jobs` schema.
- `app/providers/correction_routing.py` defines the provider-agnostic routing contract.
- New-job UX performs preview + Preflight creation in one action.
- Chirp parallelism and output formats are moved under advanced settings and browser defaults are remembered.
- Batch paid approval is one explicit amount-bearing button instead of checkbox + button.
- Production Gemini 3.7 correction code is intentionally unchanged.

## Existing MiniMax quota reference implementation — read this first

Before implementing or exploring the MiniMax Token Plan quota adapter from scratch, inspect the existing private repository:

- Repository: `Randytsay/quota-dashboard`
- Important files:
  - `server.py`
  - `api/index.py`
  - `index.html`
- Especially inspect commit `9da3155fbe4e9cfd300d236ac7a7bab0d26fa3fd` (`fix: support MiniMax new API format (general/video) using remaining_percent`).

That repository already implements the Token Plan remaining-usage endpoint and contains compatibility work for at least two API shapes. Treat it as a reference implementation and evidence source, not as a runtime dependency.

### Known quota API behaviour already handled by `quota-dashboard`

The older response shape used model/pool names such as:

- `MiniMax-M3`
- `MiniMax-M*`

and count fields such as:

- `current_interval_total_count`
- `current_interval_usage_count`
- `current_weekly_total_count`
- `current_weekly_usage_count`

A later response shape observed by the existing dashboard used broader pool names such as:

- `general`
- `video`

and the meaningful quota fields moved to percentage-based values such as:

- `current_interval_remaining_percent`
- `current_weekly_remaining_percent`

The response also uses time-window fields including:

- `start_time`
- `end_time`
- `weekly_start_time`
- `weekly_end_time`
- in some versions, `remains_time`

Do not assume that `model_name == "MiniMax-M3"` is the only valid representation of the M3-capable text pool. The quota adapter should normalize both the old model-specific form and the newer shared/general pool form into one internal text-token-plan quota snapshot.

### Required architectural rule

Do **not** make `course-transcript-mvp` depend on the running `quota-dashboard` HTTP service.

Preferred design:

```text
MiniMax Token Plan API
        ↓
MiniMaxQuotaClient
        ↓
normalize old/new response shapes
        ↓
MiniMaxQuotaSnapshot
        ↓
M3QuotaState
AVAILABLE / UNAVAILABLE / UNKNOWN
        ↓
correction_routing.py
```

`quota-dashboard` is a source of proven parsing knowledge. Copy/adapt the minimal provider-independent parsing concepts into this repository rather than adding a cross-service dependency.

### Safety differences from the dashboard implementation

The dashboard contains behaviours that are acceptable for visualization but are **not acceptable** for production model routing:

1. **Never use mock quota data for routing.**
   - The old dashboard can return `_mock_data()` when credentials/provider access are unavailable.
   - The transcript service must instead map any inability to verify live quota to `M3QuotaState.UNKNOWN`.
   - `UNKNOWN` must route to Gemini 3.7.

2. **Use a much shorter routing cache than the dashboard.**
   - The dashboard uses a 5-minute display cache.
   - For model routing, perform a live check at the beginning of each source job or use only a short bounded cache (target roughly 30–60 seconds unless runtime evidence supports another value).
   - A provider-reported quota-exhausted condition must immediately invalidate any cached `AVAILABLE` state.

3. **Do not expose partial API keys.**
   - Do not reproduce debug endpoints that reveal the first characters of a secret.
   - Browser/API status endpoints may expose only safe booleans such as `configured: true/false`.

4. **Do not hardcode the historical host without revalidation.**
   - The dashboard has used MiniMax global/CN API bases and `/v1/token_plan/remains`.
   - Revalidate the currently correct endpoint, authentication method, and response schema using the actual Token Plan account in the VPS/staging runtime before enabling M3.

## Runtime work that must be completed on the VPS/staging environment

1. Validate the real MiniMax Token Plan API using the account that will run this service.
   - begin with the known `quota-dashboard` implementation rather than rediscovering the schema from zero
   - verify the currently correct API base URL and remaining-usage endpoint
   - verify model endpoint/model identifier used for M3 inference
   - verify whether the live quota pool is reported as `MiniMax-M3`, `MiniMax-M*`, `general`, or another current identifier
   - verify the exact current response schema for both interval and weekly allowance
   - determine how the rolling interval and weekly allowance are represented
   - verify reset/end timestamps and their units/time-zone semantics
   - capture actual HTTP status + sanitized JSON body for quota exhaustion, rate limiting, authentication failure, transient provider errors, and malformed responses
   - do not infer behaviour only from documentation or historical dashboard code

2. Implement a MiniMax provider adapter.
   - input contract must remain fixed subtitle segments + terminology/context
   - output must be the same structured segment schema currently consumed by Gemini correction
   - never change segment IDs, ordering, timestamps, or boundaries
   - preserve raw provider response, usage metadata, latency, attempts, and provider/model identity for audit

3. Implement a quota adapter.
   - reuse/adapt the old/new response normalization logic learned from `Randytsay/quota-dashboard`
   - normalize the M3/general text pool into one internal quota snapshot rather than coupling routing to one literal `model_name`
   - support count-based old responses and percentage-based newer responses
   - check Token Plan availability before starting an `M3_FIRST` source job
   - map the live provider response to `M3QuotaState.AVAILABLE`, `UNAVAILABLE`, or `UNKNOWN`
   - `AVAILABLE`: both interval and weekly allowance are positively available according to the current verified schema
   - `UNAVAILABLE`: either interval or weekly allowance is explicitly exhausted
   - `UNKNOWN`: missing/ambiguous fields, provider failure, parse failure, stale data beyond the bounded TTL, or any state that cannot safely prove availability
   - `UNKNOWN` must safely start on Gemini rather than guessing that M3 is available
   - use no mock quota data in routing decisions
   - cache quota status only for a short bounded period and invalidate the cache immediately on a real quota-exhausted provider response

4. Wire routing into correction execution.
   - read requested policy with `get_job_correction_policy(...)`
   - call `choose_initial_route(...)`
   - `GEMINI_FIRST`: use Gemini 3.7 only; do not consume M3 quota
   - `M3_FIRST` + available quota: start M3
   - `M3_FIRST` + unavailable/unknown quota: start Gemini 3.7
   - once an M3 job switches to Gemini because quota is exhausted, do not switch back to M3 inside that same source job
   - re-check M3 availability only when the next source job begins

5. Map failures through `decide_provider_failure(...)`.
   - rate limit: bounded retry/backoff first
   - usage/weekly allowance exhausted: switch the rest of the current job to Gemini and invalidate cached M3 availability
   - transient failures after bounded retries: switch rest of job to Gemini
   - invalid response after validation/retry: switch rest of job to Gemini
   - authentication/configuration failure: surface the configuration error; do not silently hide it as a quota fallback
   - Gemini failures must continue through the existing raw-Chirp fallback/QA contract

6. Keep existing safety mechanisms.
   - Chirp 3 raw text and timestamps remain immutable
   - use provider-independent semantic/content guards before accepting any correction
   - deterministic subtitle cleanup remains deterministic
   - Drive publishing and QA gates must not be weakened
   - retain per-window audit and a job-level routing manifest

## Required quota normalization tests

In addition to provider/routing tests, add fixtures covering the historical quota response shapes found in `Randytsay/quota-dashboard`.

At minimum test:

1. Old count-based M3/text pool with both interval and weekly remaining → `AVAILABLE`.
2. Old count-based interval exhausted → `UNAVAILABLE`.
3. Old count-based weekly exhausted → `UNAVAILABLE`.
4. New `general` percentage-based pool with both remaining percentages > 0 → `AVAILABLE`.
5. New `general` pool with interval remaining 0 → `UNAVAILABLE`.
6. New `general` pool with weekly remaining 0 → `UNAVAILABLE`.
7. Unknown/renamed pool without a safely identifiable text-token-plan quota → `UNKNOWN`.
8. Missing required fields → `UNKNOWN`.
9. Malformed provider response → `UNKNOWN`.
10. Provider/network error → `UNKNOWN`.
11. Stale cached `AVAILABLE` state beyond TTL → force recheck or `UNKNOWN`, never assume availability.
12. Quota-exhausted inference response invalidates cached `AVAILABLE` immediately.

Do not write tests that depend on mock quota data being considered real availability.

## Required job-level routing audit

Persist enough evidence to answer:

```json
{
  "requested_policy": "M3_FIRST",
  "initial_provider": "minimax-m3",
  "m3_quota_state_at_start": "available",
  "m3_quota_checked_at": "...",
  "m3_quota_source_pool": "general",
  "m3_interval_remaining": "...",
  "m3_weekly_remaining": "...",
  "m3_interval_reset_at": "...",
  "m3_weekly_reset_at": "...",
  "provider_switches": [
    {
      "from": "minimax-m3",
      "to": "gemini-3.7-flash",
      "reason": "usage_limit",
      "at_segment_id": "...",
      "at": "..."
    }
  ],
  "segment_counts": {
    "minimax-m3": 0,
    "gemini-3.7-flash": 0,
    "chirp-3-raw": 0
  }
}
```

Use normalized/safe quota values only. Do not store provider credentials, API keys, bearer tokens, full authorization headers, or an unredacted provider response containing sensitive account data in audit files.

## Validation gate before enabling M3

- Run the complete existing Python/Next.js/Docker CI.
- Smoke-test the production API and both worker strategies with `MINIMAX_M3_ENABLED=false`; behaviour must remain Gemini-only.
- Enable M3 only in isolated/staging runtime first.
- Compare the current live quota response against the historical old/new shapes in `quota-dashboard`; document any new drift before coding assumptions around it.
- Use several previously completed real Traditional-Chinese courses as A/B fixtures.
- Compare Gemini baseline vs M3 for terminology accuracy, missing content, hallucinated rewriting, content-guard rejection rate, uncertain-term rate, latency, and human edits required.
- Test a controlled quota-exhaustion path and confirm the current source switches once to Gemini and completes without timestamp/segment drift.
- Confirm quota exhaustion invalidates an `AVAILABLE` cache immediately.
- Test that the next source checks quota again rather than inheriting the prior job's circuit breaker.
- Only after these gates pass should `MINIMAX_M3_ENABLED=true` be introduced to production.

## Production cutover rule

Do not merge or deploy a change that turns on M3 by default in the same step that first introduces the provider adapter. Provider integration, isolated validation, A/B evidence, quota-normalization evidence, and production enablement must remain separate reviewable steps.
