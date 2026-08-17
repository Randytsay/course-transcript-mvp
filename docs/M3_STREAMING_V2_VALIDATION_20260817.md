# MiniMax M3 Streaming 2.0 validation — 2026-08-17

## Decision

The strict Streaming 2.0 protocol passed the bounded 11-window diagnostic.
It is **not** enabled in production and does not change the current routing:

```text
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
READY_FOR_M3_PRODUCTION=NO
```

The earlier Phase C `usage=null` result must not be treated as a MiniMax
limitation. This run explicitly sent `stream_options.include_usage=true` and
received usage for every valid window.

## Why this diagnostic was run

Thinking-disabled non-streaming M3 passed the bounded gates and the 10-minute
comparison, but the three-course long-run comparison still had 541/553 valid
M3 windows. One course was 145/154, with transient provider failures near the
old 60-second boundary. Gemini completed 553/553. Because the long-run result
was not clean enough for production, this isolated streaming diagnostic was
run as the next conditional step.

## Immutable test and request contract

- Runtime image/release: `9e566068a287cef4db3f7b1c98302399f5c35a04`
- Source: the existing immutable 260815 subtitle fixture
- Source SHA-256: `9866acfb806243caf403dbfc778229b9d2bd2f44a4caadd937367c71c8d564ac`
- Scope: first 107 source segments, replaying the same 11 varied windows used
  by the earlier Phase C/Phase D diagnostics
- Model: `MiniMax-M3`
- `stream=true`
- `stream_options={"include_usage":true}`
- `thinking={"type":"disabled"}`
- `reasoning_split=true`
- `max_completion_tokens=4096`
- `temperature=0`

The validator used a parent-process total wall-clock deadline of 75 seconds
per request. The 5-second socket read timeout was only a read-polling guard;
it was not used as the total deadline. If the child process exceeded the total
deadline, the parent terminated it and discarded all partial output.

Every response was rejected and discarded if any of these checks failed:

1. HTTP/provider response status;
2. malformed SSE event;
3. final `finish_reason` not exactly `stop`;
4. missing usage despite `include_usage=true`;
5. invalid JSON or missing structured content;
6. changed, missing, duplicated, or reordered segment IDs;
7. emitted timestamps or other immutable fields.

## Result

| Metric | Result |
|---|---:|
| Valid windows | 11/11 |
| HTTP 200 windows | 11/11 |
| `finish_reason=stop` | 11/11 |
| Usage available | 11/11 |
| Hard-deadline exceeded | 0/11 |
| Partial outputs discarded | 0/11 |
| SSE/transport/JSON/invariant errors | 0 |
| Reasoning tokens | 0 |
| Latency P50 | 3,229 ms |
| Latency P95 | 4,824 ms |
| Latency maximum | 7,293 ms |

The sanitized runtime artifact is retained at:

```text
/opt/course-transcript-source/data/m3-validation/phase-d-thinking-disabled-20260817/streaming-v2-20260817.json
```

It contains hashes, counts, timings, request metadata, and validation flags
only. Raw transcript text, prompts, SSE bodies, and provider response bodies
were not persisted.

## Production readback after the run

- All five application services remained on the exact release image above.
- API and frontend health checks remained healthy.
- M3 remained disabled and the default correction policy remained
  `GEMINI_FIRST`.
- The read-only job check reported zero active or leased jobs.
- No Drive mutation, subtitle write-back, task submission, or production
  correction was initiated by this diagnostic.

## Follow-up gate

This proves that the corrected Streaming 2.0 accounting/deadline protocol can
work for the bounded varied sample. It does not erase the long-course
reliability gap. A future production proposal must implement the same guards
in a separately reviewed adapter, repeat the full multi-course A/B with
per-course reliability gates, retain raw results separately from corrected
results, and keep M3 off until that gate passes.
