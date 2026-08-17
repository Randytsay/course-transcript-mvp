# MiniMax M3 Streaming 2.0 adapter

This adapter implements the strict protocol validated on 2026-08-17 without enabling M3 in production.

## Safety defaults

```text
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
MINIMAX_M3_STREAMING_ENABLED=false
MINIMAX_M3_STREAM_DEADLINE_SECONDS=75
```

The production Compose files intentionally do **not** pass the new streaming variables yet. Long-course validation must use an isolated container/runtime with explicit `-e` overrides. A later, separately reviewed cutover change may expose the variables to the production pipeline worker only after the long-course gate passes.

## Streaming contract

Correction-only requests use:

```text
stream=true
stream_options.include_usage=true
thinking.type=disabled
reasoning_split=true
max_completion_tokens=4096
```

Terminology extraction remains on the existing non-stream adaptive-thinking path.

A parent process owns a hard total wall-clock deadline. The provider child owns all partial SSE output and sends content to the parent only after the stream finishes. If the child exceeds the deadline, crashes, returns malformed SSE, or otherwise fails before a complete result, partial content is discarded.

A streamed correction is accepted only when all of these are true:

1. transport/provider status succeeds;
2. `finish_reason` is exactly `stop`;
3. usage metadata is present;
4. final content is non-empty;
5. model JSON contains only the expected top-level `segments` field;
6. every segment contains only `segment_id`, `corrected_text`, and optional `uncertain_terms` — emitted timestamps or other extra/immutable fields are rejected;
7. segment count and ID order exactly match the immutable Chirp source window;
8. corrected text types/schema are valid;
9. content guard passes or falls back that segment to immutable Chirp raw text.

`finish_reason=length` is classified as `OUTPUT_LIMIT`. Authentication and quota errors retain existing fail-closed semantics. Transient/deadline failures use the existing bounded retry budget, then the source-level runtime performs the existing one-way M3-to-Gemini switch.

Deterministic tests cover malformed SSE, parent hard-deadline/partial discard, output-limit discard, missing usage, transport exhaustion, HTTP 401 fail-closed, forbidden timestamp/extra fields, ID reordering, terminology non-stream behavior, and routing/performance provenance.

## Required live gate before any production cutover

Use the merged/PR exact SHA and the same immutable Chirp segments for the prior three long-course comparison. Require per-course reliability, not only aggregate reliability. The previously weakest course was 145/154 (~94.16%), so it must be retested explicitly.

Minimum gate:

- every course >=95% complete valid M3 windows;
- aggregate >=95%;
- zero timestamp/segment-order corruption;
- zero partial output accepted;
- zero authentication silent fallback;
- usage present for accepted streaming calls;
- bounded deadline behavior proven;
- quality not materially worse than Gemini;
- Gemini-safe path regression PASS.

Until that gate passes, keep M3 and quota checking disabled in production.
