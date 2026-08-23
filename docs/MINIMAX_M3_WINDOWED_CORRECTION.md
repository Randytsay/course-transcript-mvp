# MiniMax M3 Windowed Correction

## Purpose

This change supersedes the runtime direction explored in PR #50. The product goal remains the same: use the already-paid MiniMax M3 Token Plan for subtitle text correction while keeping Chirp 3 as the immutable ASR/timestamp source.

The implementation is built on the unified correction provider router merged in PR #62. It does **not** introduce another correction runtime and does **not** enable Streaming 2.0.

## Invariants

- Chirp segment IDs, order, start times, and end times remain immutable.
- MiniMax is text-only correction.
- Every accepted model response must pass the shared exact-segment validation.
- No fake MiniMax Batch mode is exposed.
- No failed provider HTTP response body is persisted by the new window policy.
- Production deployment/cutover is not part of this PR.

## MiniMax request policy

Correction requests use:

- `thinking={"type":"disabled"}`
- `reasoning_split=true`
- `max_completion_tokens=4096`
- prompt-enforced canonical JSON array
- shared server-side strict parser/validator

The previous `response_format={"type":"json_object"}` is intentionally removed. The canonical correction response is an array and MiniMax is registered as `supports_native_schema=false`; sending a JSON-object constraint was contradictory and could provoke provider-side request/format failures.

## Realtime window policy

MiniMax correction is split deterministically by both:

- maximum 24 subtitle segments per window; and
- maximum 8,000 source characters per window.

The character bound is a deterministic tokenizer-independent proxy for Chinese subtitle payload size.

### Per-window retry

A window gets at most two attempts total (one bounded retry).

Retryable once:

- rate limit
- network/provider unavailable
- timeout
- unknown transport/service error
- malformed/invalid model response

Not blindly retried:

- invalid request / HTTP 4xx request-shape errors
- output token limit
- authentication failure

### Per-window fallback

With `RAW_CHIRP_FALLBACK`, a failed content/request-specific window keeps the exact Chirp source text for only that window. Later windows continue to use M3.

This replaces PR #50's observed course-level one-way fallback, where one failed request caused the remaining unsent windows to bypass M3.

### Provider circuit breaker

Three consecutive transport/service failed windows open the MiniMax circuit for the rest of that course. Remaining windows keep raw Chirp text without making further MiniMax requests.

Content-specific failures such as invalid JSON, invalid request, or output limit do **not** open the provider circuit because they do not prove that later windows will fail.

Authentication/quota-class failures remain course-fatal and are never hidden as successful correction.

## Validation in this PR

Mock-only tests cover:

- deterministic MiniMax request shape;
- removal of contradictory `json_object` response format;
- `finish_reason=length` rejection;
- safe HTTP 422 classification without leaking provider response text;
- deterministic segment/character window splitting;
- one bad middle window falling back while later windows still use M3;
- bounded retry after malformed JSON;
- circuit breaker after consecutive transport failures;
- immediate fatal handling of authentication failure.

No paid provider call is made by repository tests.

## Live gate before production cutover

A separate explicit validation step is required on the VPS/current MiniMax Token Plan credentials before any production enablement:

1. Validate the exact PR head on ARM64.
2. Replay the known PR #50 blocker window around `seg-0338`.
3. Replay the retained varied-window corpus.
4. Run the same three long courses used in PR #50.
5. Require zero segment/timestamp corruption and 100% safe final completion.
6. Measure MiniMax attempted-request acceptance and route coverage separately.
7. Require per-course and aggregate M3 route coverage >=95% unless failures are explicitly attributable to raw-fallback policy.
8. Compare corrected text quality against retained Chirp/Gemini evidence.
9. Keep production settings unchanged until this live gate passes.

Streaming remains a later transport optimization only if non-stream windowed M3 fails the reliability/latency gate. If needed, Streaming must be implemented inside the MiniMax provider rather than as a second correction runtime.
