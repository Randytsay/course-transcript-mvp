# PR #50 Streaming 2.0 — Course-B `seg-0338` validation

Date: 2026-08-17 (Asia/Taipei)

This is a sanitized follow-up report for the current PR #50 draft head. It
contains metadata only; no transcript text, prompt/response body, API key, or
other secret is retained.

## Scope and safety

- Repository: `Randytsay/course-transcript-mvp`
- Code head validated: `fa699e0b56dfad1cafbcb5d715061f2de4f84302`
- Job: `09-20260510-20260808-154719-24752c`
- Exact source SHA-256: `816f4fb9c113692bf2a0e26ad9422da11a2a416e586db604117e1902cdd19100`
- Source shape: 3,287 retained segments and 154 windows
- Reproduction window: index 15, first segment `seg-0338`
- Window source SHA-256: `daade19e345a726edc97e04165404674106d6aeff2e4e5f1c8dd3512d458dd57`
- Window segment-ID SHA-256: `8468afd79091dc08f4383db517cb45555d8ff89c920319f4020487b017e4e728`
- No Chirp run, Drive write, approved-output write, or production job was used.

Production remained unchanged throughout validation:

```text
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
MINIMAX_M3_STREAMING_ENABLED=off/absent
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
```

## ARM64 tests

The exact current source tree was mounted read-only into the ARM64 validation
image. The tagged current-head image was then used directly for the final
verification:

- Focused suite: **19/19 passed**
- Full Python suite: **230/230 passed**
- No validation container remained running after the tests.

## Exact reproduction result

The immediately preceding window (index 14, first segment `seg-0313`) was a
successful control: HTTP 200, one attempt, 59 SSE events, usage available,
`finish_reason=stop`.

The exact index-15 window was reproduced twice as two independent logical
reproductions. Each logical reproduction had the current adapter's bounded
outer retry:

```text
ORIGINAL_422_REPRODUCED=YES
HTTP_STATUS=422
PROVIDER_ERROR_CODE=MISSING/UNKNOWN
FAILURE_KIND=invalid_response
ATTEMPTS=2 per logical reproduction (retry_count=1)
BOUNDED_RETRY_WORKED=NO
SSE_EVENT_COUNT=0
DONE_SEEN=false
USAGE_AVAILABLE=false
FINISH_REASON=null
DEADLINE_EXCEEDED=false
```

Sanitized provider diagnostics were:

| Reproduction | Provider trace ID | Error fingerprint | Error bytes |
|---|---|---|---:|
| 1 | `06d25a052d4d331e04770123c73aad5e` | `b712ebd1779f7d161d2bf28a` | 168 |
| 2 | `06d25a2fc0edc718f2a00a0f8a12d99e` | `49589d3249e5545ec20b5f45` | 168 |

Both responses were HTTP 422 with no provider error code. The current adapter
therefore used the unknown-code fallback (`invalid_response`) and performed
one bounded retry; neither retry succeeded. Because the provider code is
missing, the evidence cannot distinguish a provider-side permanent parameter
or content rejection from a transient provider error. No root cause is being
invented from this response.

The request metadata does not show an obvious size or output-limit trigger:
the failing window was smaller than the successful control (20 vs 25
segments, 2,288 vs 2,519 request bytes, estimated input 388 vs 436 tokens),
with the same `MiniMax-M3`, streaming, `include_usage`, disabled-thinking,
reasoning-split, and 4,096 maximum-completion-token settings. This is a
metadata inference, not proof of provider behavior.

## Gate decision

The exact original blocker remains unresolved after the allowed second
reproduction. Therefore the 154-window Course-B rerun and the three-course
gate were not started. Prior route/attempt numbers from the older validation
head are not re-labeled as current-head results:

```text
COURSE_B_ROUTE_COVERAGE=NOT_RUN (prior 2ff evidence: 15/154 = 9.74%)
COURSE_B_M3_ATTEMPT_ACCEPTANCE=NOT_RUN (prior 2ff evidence: 15/16 = 93.75%)
THREE_COURSE_GATE=NOT_RUN
AGGREGATE_ROUTE_COVERAGE=NOT_RUN
AGGREGATE_M3_ATTEMPT_ACCEPTANCE=NOT_RUN
PR50_READY_TO_MERGE=NO
READY_FOR_M3_PRODUCTION=NO
PRODUCTION_CHANGED=NO
```

PR #50 must remain Draft and M3 must remain disabled. The narrow next action
is to resolve the missing provider code using the two trace IDs/fingerprints,
or obtain a provider response that includes the code; a larger streaming
architecture change is not justified by this evidence.
