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

## Runtime work that must be completed on the VPS/staging environment

1. Validate the real MiniMax Token Plan API using the account that will run this service.
   - verify model endpoint/model name
   - verify the remaining-usage endpoint and exact response schema
   - determine how the 5-hour rolling allowance and weekly allowance are represented
   - capture actual HTTP status + JSON body for quota exhaustion, rate limiting, authentication failure, transient provider errors, and malformed responses
   - do not infer behaviour only from documentation

2. Implement a MiniMax provider adapter.
   - input contract must remain fixed subtitle segments + terminology/context
   - output must be the same structured segment schema currently consumed by Gemini correction
   - never change segment IDs, ordering, timestamps, or boundaries
   - preserve raw provider response, usage metadata, latency, attempts, and provider/model identity for audit

3. Implement a quota adapter.
   - check Token Plan availability before starting an `M3_FIRST` job
   - map the live provider response to `M3QuotaState.AVAILABLE`, `UNAVAILABLE`, or `UNKNOWN`
   - `UNKNOWN` must safely start on Gemini rather than guessing that M3 is available
   - cache quota status only for a short bounded period

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
   - usage/weekly allowance exhausted: switch the rest of the current job to Gemini
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

## Required job-level routing audit

Persist enough evidence to answer:

```json
{
  "requested_policy": "M3_FIRST",
  "initial_provider": "minimax-m3",
  "m3_quota_state_at_start": "available",
  "m3_quota_checked_at": "...",
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

Do not store provider credentials, API keys, bearer tokens, or full authorization headers in audit files.

## Validation gate before enabling M3

- Run the complete existing Python/Next.js/Docker CI.
- Smoke-test the production API and both worker strategies with `MINIMAX_M3_ENABLED=false`; behaviour must remain Gemini-only.
- Enable M3 only in isolated/staging runtime first.
- Use several previously completed real Traditional-Chinese courses as A/B fixtures.
- Compare Gemini baseline vs M3 for terminology accuracy, missing content, hallucinated rewriting, content-guard rejection rate, uncertain-term rate, latency, and human edits required.
- Test a controlled quota-exhaustion path and confirm the current source switches once to Gemini and completes without timestamp/segment drift.
- Test that the next source checks quota again rather than inheriting the prior job's circuit breaker.
- Only after these gates pass should `MINIMAX_M3_ENABLED=true` be introduced to production.

## Production cutover rule

Do not merge or deploy a change that turns on M3 by default in the same step that first introduces the provider adapter. Provider integration, isolated validation, A/B evidence, and production enablement must remain separate reviewable steps.
