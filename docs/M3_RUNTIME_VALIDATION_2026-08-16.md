# MiniMax M3 runtime validation — 2026-08-16

## Current decision

The MiniMax Token Plan quota path is validated for the configured account, but
M3 correction remains disabled in production. A successful quota response is
not treated as proof that the inference endpoint, model identifier, or
correction quality is ready for production.

## Read-only VPS evidence

- Secret path: `/opt/course-transcript/secrets/minimax-api-key` (root-owned;
  the value was never printed or copied into the repository).
- CN quota endpoint: `https://api.minimaxi.com/v1/token_plan/remains`.
- HTTP result: `200`, provider `base_resp.status_code=0`.
- Sanitized text-pool result: `model_name=general`, interval remaining `90%`,
  weekly remaining `69%`.
- The same response also contained `model_name=video` with `100%`/`100%`;
  the routing adapter ignores this non-text pool.
- Global quota endpoint returned HTTP `200` with provider status `2049`
  (`invalid api key`), so the global endpoint is not used for this account.
- CN inference canary: `POST https://api.minimaxi.com/v1/chat/completions`
  with model `MiniMax-M3` returned HTTP `200`, provider status `0`, one choice,
  and readable usage metadata. The response included a `<think>...</think>`
  wrapper before the final content; the adapter removes that wrapper before
  structured JSON validation.
- Fixed-segment canary passed: returned JSON was parseable, the original
  segment ID was preserved exactly, and no timestamp fields were returned by
  MiniMax.
- Post-canary quota refresh remained healthy: `general` interval `90%`, weekly
  `69%`, HTTP `200`, provider status `0`.
- The VPS runtime flags remain `MINIMAX_M3_ENABLED=false` and
  `MINIMAX_M3_QUOTA_CHECK_ENABLED=false`.

## Safeguards now in source

- The quota client defaults to the validated CN endpoint and supports both the
  historical count fields and the newer `general` percentage fields.
- Missing, ambiguous, stale, malformed, or unreachable quota data becomes
  `UNKNOWN`, which routes to Gemini 3.7 rather than guessing availability.
- The key is mounted read-only only into the API and pipeline-worker services;
  status responses expose only safe booleans and normalized quota state.
- An M3 usage/rate/transient/invalid failure can switch the remainder of one
  source job to Gemini once; the same job never re-enters M3.
- Raw Chirp segments, IDs, ordering, timestamps, and boundaries remain
  immutable. Provider responses, usage, latency, attempts, and routing are
  audited without credentials.

## Validation completed

- Full Python suite: `182/182` passed.
- MiniMax quota/provider/runtime regression tests: `17/17` passed.
- Main and isolated smoke Docker Compose configuration: passed.
- `git diff --check`: passed.

## Required next gate before any M3 enablement

Run a bounded non-production comparison against Gemini 3.7 for terminology
accuracy, missing content, semantic drift, raw fallback rate, uncertain terms,
latency, token usage, and human edits. Only after that review may a separate
change enable M3; this validation does not enable it.
