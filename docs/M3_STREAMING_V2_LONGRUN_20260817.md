# PR #50 MiniMax M3 Streaming 2.0 VPS Gate — 2026-08-17

## Decision

```text
PHASE_STATUS=FAIL
PR50_CODE_HEAD=2ff802349411efdf0ba22abfe74f044136d5d238
PR50_READY_TO_MERGE=NO
PR50_MERGED=NO
CUTOVER_PR=NOT_CREATED
READY_FOR_M3_PRODUCTION=NO
PRODUCTION_CUTOVER_COMPLETED=NO
```

The code-side checks passed, but the live long-course gate did not meet the
required 95% native M3 route-coverage threshold. Production therefore remains
on the safe Gemini-first baseline with M3 and M3 streaming disabled.

## Reproducibility and isolation

- VPS architecture: ARM64 (`aarch64`).
- VPS source SHA at start and after validation: `55eabdb12dc65c7dc2b19ce30732b7e54e224059`.
- Isolated PR code SHA: `2ff802349411efdf0ba22abfe74f044136d5d238`.
- Isolated image: `course-transcript-pr50-validation:2ff802349411efdf0ba22abfe74f044136d5d238`.
- Isolated image ID: `sha256:931698df20450306096c25a933407345dd5e3928ca6bc37e9798d28c1535a7c6`.
- PR #50 was built and run separately from production. No production container
  was replaced or restarted.
- Exact-head ARM64 streaming tests: 13/13 passed.
- Exact-head ARM64 Python suite: 224/224 passed.
- Live quota pre-check: HTTP 200, `available`, general pool, interval 99%
  remaining, weekly 98% remaining.
- Test flags in the isolated run: M3 enabled, quota check enabled, streaming
  enabled, deadline 75 seconds, `M3_FIRST`, thinking disabled, reasoning split
  enabled, max output 4096, CN endpoint.

## Eleven-window replay

Source job: `260815-20260816-152635-39ffbc`  
Source SHA-256:
`9866acfb806243caf403dbfc778229b9d2bd2f44a4caadd937367c71c8d564ac`

```text
windows=11
native_m3_valid=11/11 (100%)
usage_accepted=11/11
finish_reason_stop=11/11
hard_deadline_failures=0
output_limit_failures=0
SSE/schema_failures=0
segment_invariant_failures=0
timestamp_invariant_failures=0
same_source_m3_reentry=0
transport_retries=0
latency_native_m3_only=P50 4331 ms / P95 7871 ms / max 7871 ms
```

The routing manifest recorded `m3_streaming_enabled=true`,
`m3_transport=streaming_v2`, and `m3_stream_deadline_seconds=75`.

Quality comparison was limited to the retained exact Gemini 3.7 ten-minute
baseline: 14 comparable segments, 14 equal, mean ratios all 1.0. No new
Gemini calls were made and no human reference was available.

## Three-course long-run gate

All runs used the required source job IDs, source hashes, segmentation windows,
and the PR #50 adapter. Latencies below are for accepted native M3 windows
only.

| Course | Windows | Native M3 accepted | Route coverage | Final safe completion | Usage accepted | P50/P95/max | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| `260801-1934-20260801-205446-9e6ecc` | 205 | 205 | 100.00% | 205/205 | 205/205 | 5050 / 10692 / 44346 ms | pass |
| `09-20260510-20260808-154719-24752c` | 154 | 15 | 9.74% | 154/154* | 15/15 | 5198 / 19936 / 19936 ms | **fail** |
| `260718-20260801-100405-f76e03` | 194 | 194 | 100.00% | 194/194 | 194/194 | 4659 / 9246 / 12405 ms | pass |
| **Aggregate** | **553** | **414** | **74.86%** | **553/553*** | — | — | **fail** |

`*` Final completion in this isolated report uses the intentionally configured
raw fallback stub. It proves bounded one-way completion and invariant safety;
it does **not** claim that Gemini was called. Actual Gemini calls during this
long run: `0`.

### Metric interpretation correction

The `414/553 = 74.86%` figure above is **M3 route coverage under the required
one-way source fallback policy**, not an independent per-request MiniMax
success rate. Course B stopped attempting M3 immediately after its first failed
window, so the remaining 139 Course-B windows were never sent to MiniMax.

Observed M3 request-level evidence from this run is therefore:

```text
course_a_m3_attempted=205
course_a_m3_accepted=205
course_b_m3_attempted=16
course_b_m3_accepted=15
course_c_m3_attempted=194
course_c_m3_accepted=194
aggregate_m3_attempted=415
aggregate_m3_accepted=414
aggregate_observed_attempt_acceptance=414/415=99.76%
course_b_observed_attempt_acceptance=15/16=93.75%
aggregate_route_coverage=414/553=74.86%
```

This distinction does **not** change the gate decision: the validated routing
contract intentionally switches the rest of a source away from M3 after a
non-retryable failure, so Course B still fails the production-readiness route
coverage requirement. It does prevent the 74.86% figure from being misread as
139 additional MiniMax request failures.

### Course B failure detail

The first failure was window index 15 at source segment `seg-0338`:

```text
HTTP status=422
failure_kind=invalid_response
stream_event_count=0
stream_done_seen=false
stream_usage_available=false
finish_reason=none
deadline_exceeded=false
transport_retries=0
provider_switches=1 (minimax-m3 -> gemini-3.7-flash)
same_source_m3_reentry=0
```

The runtime then stayed on the fallback route for the remaining 139 windows.
The corrected report has one SSE/schema invalid-response failure and one
missing-usage rejection for the failed M3 attempt; it does not count the
fallback rows' inherited metadata as additional M3 failures. There were no
authentication, quota, deadline, output-limit, timestamp, or segment-ID
invariant failures. Content-guard rejections were 0 for this course; the 2950
raw fallback segments are reported separately.

The original PR #50 code under test only retained the HTTP 422 wrapper status
for this failure. A subsequent reviewer diagnostic patch on the same Draft PR
adds safe MiniMax provider-error-code, trace-ID, and error-fingerprint evidence
so the next exact-window reproduction can distinguish retryable provider/system
errors from permanent parameter/content/token-plan failures without retaining
transcript, prompt, or response-body text.

## Production safety recheck

```text
PRODUCTION_BASELINE_IMAGE=9e566068a287cef4db3f7b1c98302399f5c35a04
PRODUCTION_MINIMAX_M3_ENABLED=false
PRODUCTION_MINIMAX_M3_QUOTA_CHECK_ENABLED=false
PRODUCTION_MINIMAX_M3_STREAMING_ENABLED=false (unset, therefore off)
PRODUCTION_CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
PRODUCTION_CANARY=NOT_RUN
PRODUCTION_ROLLBACK=NOT_REQUIRED
```

The API/frontend were healthy; production containers remained on the baseline
revision. The production source tree and its pre-existing unrelated dirty
files were not modified by this validation. No public link, IAM/firewall
change, Drive write, or production M3 call was performed.

## Sanitized evidence

The source report was preserved and a corrected report was written without
transcript text, prompts, response bodies, or credentials:

```text
/opt/course-transcript-source/data/m3-validation/phase-d-thinking-disabled-20260817/pr50-streaming-v2-20260817/report.json
/opt/course-transcript-source/data/m3-validation/phase-d-thinking-disabled-20260817/pr50-streaming-v2-20260817/report.corrected.json
```

The corrected report separates native M3 acceptance, fallback routing, raw
fallback segments, content guards, and actual Gemini calls. The report is
sanitized for review and is not a production transcript export.

## Required next gate

Do not mark PR #50 ready, merge it, or create a production cutover PR from this
run. The next run must first reproduce and explain the HTTP 422 at `seg-0338`
using the diagnostic-capable current PR head, then rerun the exact 11-window and
three-course gates. Production flags should remain unchanged until every course
and the aggregate reach at least 95% M3 route coverage with accepted usage and
stop finish reasons.
