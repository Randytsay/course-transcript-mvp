# MiniMax M3 Phase B validation — 2026-08-17

## Scope

This is a sanitized, repository-retained summary of the bounded Phase B validation.
It intentionally excludes transcript text, credentials, authorization headers, and private source names.
The detailed provider evidence remains on the VPS under:
`/opt/course-transcript-source/data/m3-validation/phase-b-20260817/`.

## Runtime and root cause

- Validation base before PR #43 deployment: `3385c6d58ea571a4ea399711762fa9c515560d4f`.
- MiniMax endpoint/account: configured CN Token Plan account; no credential material is retained here.
- Confirmed root cause of the earlier invalid JSON failures: MiniMax-M3 reasoning was embedded in `message.content` and could consume the configured `4096` completion-token budget before final structured JSON was produced.
- Capability canary confirmed `reasoning_split=true` separates provider reasoning from final `message.content` on the actual configured MiniMax-M3 account.
- The fix does **not** increase `MINIMAX_M3_MAX_OUTPUT_TOKENS`.

## 10-minute immutable-segment A/B

The same pre-existing Chirp segments and timestamps were reused; Chirp was not rerun.

| Provider | Valid | Transport timeout | Output-limit hit |
|---|---:|---:|---:|
| Gemini 3.7 Flash | 10/10 | 0 | 0 |
| MiniMax M3 | 4/10 | 6/10 | 0 |

- MiniMax provider timeout limit during this validation: **60 seconds**.
- The reasoning/output-ceiling failure was no longer observed after `reasoning_split=true`.
- M3 transport reliability did not meet the production gate.

## Long-course source samples

Three existing long-course sources were sampled using bounded **5-minute** A/B windows. These were not full-course runs.

| Sample | Gemini valid | MiniMax M3 valid | Result |
|---|---:|---:|---|
| A | 5/5 | 3/5 | M3 reliability gate failed |
| B | 5/5 | 2/5 | M3 reliability gate failed |
| C | 5/5 | 3/5 | M3 reliability gate failed |

Aggregate Gemini result: **15/15 valid**. MiniMax failures were transport timeouts; no M3 output-limit hit was reported.

**Full long-course A/B status: BLOCKED / NOT COMPLETED.**
The bounded stop gate was intentionally applied after M3 transport reliability failed, avoiding unnecessary provider consumption.

## Quota/fallback evidence

- Controlled `usage_limit` fallback E2E: PASS.
- Same source: one-way M3 → Gemini switch; no M3 re-entry.
- Raw Chirp segment identity/timing invariants: preserved.
- CN Token Plan `general` pool after bounded validation: interval remaining **96%**, weekly remaining **99%**.

## Latency and reasoning-token evidence

- P50/P95 latency values are **NOT_AVAILABLE in the PR-retained evidence** and are therefore not fabricated here.
- Historical bounded calls did not retain normalized reasoning-token counts in the performance summary. PR #43 reviewer hardening adds `usage_metadata.reasoning_tokens` and provider-performance aggregation for future calls when the provider exposes `completion_tokens_details.reasoning_tokens`.

## Production gate

`READY_FOR_M3_PRODUCTION = NO`

Reason: structured-output reliability improved, but MiniMax-M3 transport/latency reliability under the real workload remains below the production-primary threshold.

Production requirements remain:

- `MINIMAX_M3_ENABLED=false`
- `MINIMAX_M3_QUOTA_CHECK_ENABLED=false`
- Gemini remains the safe baseline.

The next investigation should measure non-stream TTFB/total latency versus streaming first-chunk/final-content timing before changing timeout or retry policy.
