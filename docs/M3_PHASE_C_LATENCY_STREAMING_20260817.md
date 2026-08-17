# MiniMax M3 Phase C — latency and streaming reliability

Date: 2026-08-17  
Repository baseline: `dee0d083befc7ac4ca694808f2b5d9970fc655e0`  
Source: existing immutable Chirp job `260815-20260816-152635-39ffbc`  
Scope: provider correction diagnostics only; no Chirp rerun, Drive mutation, or production transcript overwrite.

This report is sanitized. It contains no transcript text, credentials, authorization headers, or private source names. Detailed evidence is retained on the VPS under `/opt/course-transcript-source/data/m3-validation/`.

## Decision summary

The bounded streaming experiment did not pass the production reliability gate. Streaming materially improves first-byte observability, but it does not reliably produce final structured JSON on varied course windows. The provider can still spend tens of seconds reasoning and finish with `length` before any final JSON is emitted. Production M3 remains disabled; no streaming adapter was merged.

Production acceptance threshold used here: at least 95% valid structured windows on the bounded same-source run, no output-limit hits, transport failures at or below 5%, preserved immutable IDs/order/timestamps, no secret exposure, and a bounded worst-case wall-clock path to Gemini fallback. This is required because one invalid correction window blocks a complete correction result and increases fallback latency; “better than the previous 4/10” alone is not sufficient.

## Phase results

| Field | Result |
|---|---|
| `PHASE_STATUS` | `PARTIAL` — safe deployment and diagnostics passed; M3 production gate failed |
| `starting_vps_sha` | `3385c6d58ea571a4ea399711762fa9c515560d4f` (container/release revision; source directory had no `.git`) |
| `deployed_sha` | `dee0d083befc7ac4ca694808f2b5d9970fc655e0` |
| `production_m3_enabled` | `false` |
| `production_quota_check_enabled` | `false` |
| `default_policy` | `GEMINI_FIRST` |
| `READY_FOR_M3_PRODUCTION` | `NO` |

Deployment evidence: exact-SHA ARM64 images were built with OCI revision labels matching `dee0d083…`; the full container suite passed `208 tests ... OK`; API health returned 200, frontend returned 200 on `127.0.0.1:3300`, and pipeline/delivery restart persistence passed. The health monitor remained healthy. No cloudflared, Drive, database, Chirp, or approved output was changed.

## Latency root cause

The evidence separates two failure modes:

1. Non-stream responses can wait tens of seconds before the only response body arrives. A 14-segment probe returned HTTP 200 after 48.2 s, exhausted all 4096 output tokens, and had no final content (`finish_reason=length`). The earlier same-source 10-minute non-stream run remained 4/10 valid with 6 transport timeouts.
2. Streaming usually produces a provider event quickly, but this is not final-output reliability. On varied windows the model can continue reasoning for 38–57 s, emit no final JSON, and finish with `length`; one smaller-window probe continued to 144 s. The direct streaming collector also showed that the configured socket timeout is not a total wall-clock deadline when new chunks keep arriving.

Therefore, raising the timeout alone is not a root-cause fix. The remaining problem is provider-side reasoning/output completion reliability under real window content, plus the need for a hard total deadline if streaming is ever implemented.

## Diagnostic matrix

All requests used `MiniMax-M3`, `reasoning_split=true`, `max_tokens=4096`, the existing correction prompt shape, and immutable source IDs. The matrix stored hashes and metadata only.

### Repeated same-window diagnostic

Artifact: `/opt/course-transcript-source/data/m3-validation/phase-c-latency-20260817/matrix-60s.json`.

| Mode / window | Valid | TTFB P50 / P95 | First final P50 / P95 | Total P50 / P95 |
|---|---:|---:|---:|---:|
| non-stream / 7 segments | 2/2 | 10.44 s / 13.90 s | 10.45 s / 13.91 s | 10.45 s / 13.91 s |
| non-stream / 10 segments | 2/2 | 19.65 s / 21.49 s | 19.67 s / 21.51 s | 19.67 s / 21.51 s |
| non-stream / 14 segments | 2/2 | 30.62 s / 40.37 s | 30.67 s / 40.45 s | 30.67 s / 40.45 s |
| stream / 7 segments | 2/2 | 1.60 s / 1.94 s | 26.68 s / 41.55 s | 28.39 s / 43.30 s |
| stream / 10 segments | 2/2 | 2.40 s / 3.60 s | 12.98 s / 14.89 s | 15.43 s / 16.94 s |
| stream / 14 segments | 2/2 | 1.35 s / 1.48 s | 27.16 s / 41.17 s | 30.04 s / 44.28 s |

This repeated offset-0 sample makes stream/10 the local latency winner, but it is not sufficient for production because it does not cover varied course content.

### Bounded 10-minute varied-window stream run

Artifact: `/opt/course-transcript-source/data/m3-validation/phase-c-latency-20260817/stream-10min.json`.

The first 10 minutes contained 107 immutable segments. Eleven diagnostic windows covered all 107 segments (ten 10-segment windows plus a 7-segment tail).

| Result | Value |
|---|---:|
| valid structured windows | 6/11 (54.5%) |
| `finish_reason=length` | 5/11 (45.5%) |
| transport errors/timeouts observed by collector | 0/11 |
| TTFB P50 / P95 | 1.39 s / 3.97 s |
| first final P50 / P95 | 25.40 s / 59.62 s |
| total latency P50 / P95 | 38.26 s / 64.00 s |

The five failures had no final content. Smaller windows did not produce a safe configuration: the targeted 5/3-segment probes were only 4/8 valid, with four output-limit failures. Evidence: `/opt/course-transcript-source/data/m3-validation/phase-c-latency-20260817/small-windows.json`.

### Token and streaming accounting

The non-stream response exposed ordinary usage metadata; the initial 14-segment output-limit response reported `output_tokens=4096`. In the streaming shape probe, every event contained a `usage` key whose value was `null`; no input/output/reasoning token counts were available from the stream. Reasoning text was observed, but it must not be converted to token counts. Evidence: `/opt/course-transcript-source/data/m3-validation/phase-c-latency-20260817/stream-usage-shape-v2.json`.

This is an accounting blocker for a production streaming adapter: it must either receive reliable usage metadata from the provider or fail closed for cost/quota accounting.

## Window, terminology, and timeout decisions

- Best local diagnostic shape: `stream=true`, 10 immutable segments per request. It had 2/2 valid and the lowest repeated-sample total latency, but failed 5/11 on the varied 10-minute run. It is **not** a production configuration.
- Terminology: the prior Phase B three-sample evidence was 2/2, 4/4, and 4/4 valid for the bounded terminology checks, with no reported M3 output-limit hit. No new terminology call was needed; the current blocker is correction-window completion, not demonstrated terminology-window failure.
- Timeout: no 90 s or 120 s promotion is justified. First useful stream events are usually below 20 s, while final completion can still end in `length` and one probe continued to 144 s. Keep production routing unchanged and require a hard wall-clock deadline plus Gemini fallback in any future streaming experiment.

## A/B, quota, and quality evidence

| Field | Result |
|---|---|
| `10_minute_gemini_result` | 10/10 valid, 0 transport timeout, 0 output-limit; existing exact-source artifact `/opt/course-transcript-source/data/m3-validation/phase-b-20260817/ab-10min-full/results.json` |
| `10_minute_m3_result` | Previous non-stream: 4/10 valid, 6 transport timeout, 0 output-limit. New stream: 6/11 valid, 0 transport error, 5 output-limit. |
| `quota_fallback_e2e` | PASS in Phase B: usage-limit switched once from M3 to Gemini; no same-source M3 re-entry; prior sanitized report `docs/M3_PHASE_B_VALIDATION_20260817.md` |
| `same_source_no_reentry` | PASS in existing Phase B fallback test |
| `next_source_quota_recheck` | PASS in existing Phase B routing evidence |
| `full_long_course_ab` | NOT RUN — correctly blocked by the bounded reliability gate |
| `Gemini_vs_M3_quality` | Gemini remained 15/15 valid across the three bounded long-course samples; M3 was 3/5, 2/5, and 3/5. Human semantic edit metrics: `NOT_AVAILABLE`. |
| `Gemini_estimated_cost` | `NOT_AVAILABLE` in the retained sanitized artifacts; do not fabricate. |
| `M3_token_plan_usage` | Current read-only quota snapshot after diagnostics: `general`, interval remaining 91%, weekly remaining 99%, HTTP 200. Per-stream token usage was `NOT_AVAILABLE` because SSE usage was null. |

The quota read is informational only. It does not authorize M3 production routing.

## Code and production decision

| Field | Result |
|---|---|
| `code_changes` | Documentation-only Phase C report; no runtime streaming adapter or timeout change merged |
| `branch` | `codex/m3-latency-streaming-validation` |
| `commit_sha` | Recorded in the GitHub PR/merge metadata |
| `pr_url` | Recorded in the GitHub PR/merge metadata |
| `ci_status` | Required CI gates must pass before merge |
| `merge_sha` | Recorded after merge |
| `production_cutover` | PASS for exact approved main with M3 kept OFF |
| `production_canary` | NOT RUN — M3 readiness gate failed, so enabling canary was not authorized by the gate |
| `rollback_required` | NO |
| `rollback_performed` | NO |

`READY_FOR_M3_PRODUCTION = NO`.

Remaining risks are: provider output-limit failures on varied content, unbounded stream wall-clock behavior unless explicitly enforced, and missing stream usage metadata for Token Plan accounting. Recommendation: keep `MINIMAX_M3_ENABLED=false`, `MINIMAX_M3_QUOTA_CHECK_ENABLED=false`, and `CORRECTION_DEFAULT_POLICY=GEMINI_FIRST`; do not run the full multi-hour A/B or enable M3 until a new provider/runtime experiment demonstrates at least 95% valid bounded windows with zero output-limit hits, reliable usage accounting, and a hard total deadline.

## Required final fields

```text
PHASE_STATUS = PARTIAL
starting_vps_sha = 3385c6d58ea571a4ea399711762fa9c515560d4f
deployed_sha = dee0d083befc7ac4ca694808f2b5d9970fc655e0
production_m3_enabled = false
production_quota_check_enabled = false
default_policy = GEMINI_FIRST
latency_root_cause = provider-side reasoning/output completion instability; streaming improves TTFB but not final reliability
non_stream_60s_result = historical 4/10 valid with 6/10 transport timeouts; diagnostic small matrix 6/6 but one initial 14-segment output-limit
streaming_result = varied 10-minute run 6/11 valid, 5/11 output-limit, 0/11 transport error; SSE usage null
TTFB_P50 = 1.39s (varied 10-minute stream)
TTFB_P95 = 3.97s (varied 10-minute stream)
total_latency_P50 = 38.26s (all varied 10-minute stream windows)
total_latency_P95 = 64.00s (all varied 10-minute stream windows)
best_window_configuration = diagnostic-only stream=true, 10 segments; not production-safe
best_timeout_configuration = no promotion; 60s baseline with future hard wall-clock deadline required
streaming_recommended = NO
reasoning_tokens_observed = stream token counts unavailable; reasoning characters observed; do not infer tokens
output_limit_hits = 5/11 varied stream; 1/1 initial 14-segment non-stream probe
transport_timeout_rate = historical non-stream 6/10; fresh varied stream 0/11 transport errors
10_minute_gemini_result = 10/10 valid, 0 timeout, 0 output-limit
10_minute_m3_result = previous non-stream 4/10; fresh stream 6/11 with 5 output-limit
quota_fallback_e2e = PASS (existing Phase B)
same_source_no_reentry = PASS (existing Phase B)
next_source_quota_recheck = PASS (existing Phase B)
full_long_course_ab = NOT_RUN by stop gate
Gemini_vs_M3_quality = Gemini 15/15 vs M3 3/5, 2/5, 3/5 in existing bounded long-course samples; human edit metrics NOT_AVAILABLE
Gemini_estimated_cost = NOT_AVAILABLE
M3_token_plan_usage = quota snapshot general interval 91%, weekly 99%; stream usage NOT_AVAILABLE
code_changes = documentation-only report; no runtime change
branch = codex/m3-latency-streaming-validation
commit_sha = see GitHub metadata
pr_url = see GitHub metadata
ci_status = pending at report authoring; final status in handoff
merge_sha = see GitHub metadata
production_cutover = exact approved main deployed with M3 OFF
production_canary = NOT_RUN
rollback_required = NO
rollback_performed = NO
remaining_risks = output-limit instability, missing stream usage, no hard total stream deadline
recommendation = keep Gemini_FIRST and M3 OFF; repeat only after a provider/runtime fix
READY_FOR_M3_PRODUCTION = NO
PRODUCTION_CUTOVER_COMPLETED = YES (M3-off safety cutover)
PRODUCTION_CANARY = NOT_RUN
ROLLBACK_REQUIRED = NO
```
