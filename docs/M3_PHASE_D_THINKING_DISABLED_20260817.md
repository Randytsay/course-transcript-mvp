# MiniMax M3 Phase D — disabled-thinking correction validation

Date: 2026-08-17
Repository: `Randytsay/course-transcript-mvp`
PR head: `d392030a5a4e762190b1fab9a1c0362be9e89884`
Production baseline: `dda66f4f019409ee415a30ec89cdb482bda1c729`

This report is sanitized. It contains no transcript text, provider response
bodies, credentials, authorization headers, or private source names. The
read-only validation metadata is retained on the VPS at:
`/opt/course-transcript-source/data/m3-validation/phase-d-thinking-disabled-20260817/`.

## Scope and safety

- Validation used the exact PR #46 provider adapter in an isolated runtime.
- Production M3 stayed disabled throughout.
- The source was the immutable Chirp subtitle artifact for job
  `260815-20260816-152635-39ffbc`, SHA-256
  `9866acfb806243caf403dbfc778229b9d2bd2f44a4caadd937367c71c8d564ac`.
- No Chirp rerun, Drive mutation, production transcript overwrite, or new
  application job was performed.
- Correction used `stream=false`, `thinking={"type":"disabled"}`,
  `reasoning_split=true`, and `max_completion_tokens=4096`.
- Terminology behavior was unchanged: `thinking={"type":"adaptive"}`.

## Direct capability canary

| Field | Result |
|---|---|
| HTTP status | `200` |
| provider status | `0` |
| returned model | `MiniMax-M3` |
| `finish_reason` | `stop` |
| input tokens | `307` |
| output tokens | `84` |
| reasoning tokens | `0` |
| total tokens | `391` |
| latency | `2.144s` |
| structured JSON | valid |
| segment IDs/order | unchanged |
| timestamps emitted | no |
| content guard fallback | none |

The CN endpoint accepted both `thinking={"type":"disabled"}` and
`max_completion_tokens=4096`. Usage metadata was non-null and usable.

## Phase C replay and bounded reliability gate

The same 11 varied windows from Phase C were replayed first. Nine additional
windows reused the exact prior Phase-B boundaries, giving 20 representative
correction windows in total.

| Metric | Result |
|---|---:|
| Phase C replay | `11/11` valid |
| additional representative windows | `9/9` valid |
| total representative windows | `20/20` valid |
| valid rate | `100%` |
| `finish_reason=length` | `0/20` |
| transport errors/timeouts | `0/20` |
| HTTP/provider status | all `200` / `0` |
| usage evidence | `20/20` available |
| reasoning tokens | `0` in all successful responses |
| segment ID/order invariant | PASS |
| timestamp invariant | PASS |
| content guard fallback | `0` segments |
| max completed-window latency | `13.427s` |

Latency for the 20 non-stream completed windows was:

- P50: `4.360s`
- P95: `10.951s`
- maximum: `13.427s`

Compared with the Phase C varied streaming baseline, completed correction was
substantially faster: Phase C total latency P50/P95 was `38.26s / 64.00s`.

## Quality comparison

Nine exact prior Phase-B windows covering 101 immutable segments were compared
against the retained M3 and Gemini outputs. This is an automated comparison,
not a replacement for future human semantic review.

| Metric | Result |
|---|---:|
| valid quality-comparison windows | `9/9` |
| segments compared | `101` |
| exact match to prior Gemini output | `77/101` |
| exact match to prior valid M3 output | `25` segments where prior M3 output existed |
| mean current-vs-Gemini character similarity | `0.9778` |
| minimum current-vs-Gemini similarity | `0.75` |
| mean current-vs-raw similarity | `0.9856` |
| empty/excessive-addition warning segments | `0` |
| human semantic edit metric | `NOT_AVAILABLE` |

No structural, deletion/addition, or content-guard regression was observed.
The retained Gemini result remains the quality reference, and human review is
still required before any M3 production enablement.

## Corrected interpretation of Phase C streaming usage

Phase C established only that the previous collector, which sent ordinary SSE
streaming without `stream_options.include_usage=true`, observed `usage: null`.
It did not establish that MiniMax M3 fundamentally cannot return streaming
usage. If non-stream later regresses and streaming must be revisited, the next
experiment must use all of the following together:

```text
stream=true
stream_options={"include_usage": true}
hard wall-clock deadline
finish_reason validation
partial output discard
```

Partial output must be discarded on deadline, `finish_reason=length`, abnormal
stream termination, incomplete JSON, or segment ID mismatch. This report does
not implement streaming 2.0 because the disabled-thinking non-stream path
passed the required bounded gate.

## Decision

```text
PR46_HEAD=d392030a5a4e762190b1fab9a1c0362be9e89884
CN_THINKING_DISABLED_SUPPORTED=YES
CN_MAX_COMPLETION_TOKENS_SUPPORTED=YES
DIRECT_CANARY=PASS
PHASE_C_REPLAY=11/11
TOTAL_REPRESENTATIVE_WINDOWS=20
VALID_WINDOWS=20/20
OUTPUT_LIMIT_HITS=0
TRANSPORT_TIMEOUTS=0
VALID_RATE=100%
LATENCY_P50=4.360s
LATENCY_P95=10.951s
MAX_LATENCY=13.427s
QUALITY_VS_PREVIOUS_M3=automated structural/content checks PASS; exact-match comparison limited to prior valid M3 records
QUALITY_VS_GEMINI=automated comparison PASS; mean similarity 0.9778; human semantic metric NOT_AVAILABLE
SEGMENT_INVARIANT=PASS
TIMESTAMP_INVARIANT=PASS
USAGE_EVIDENCE=AVAILABLE, 20/20; reasoning_tokens=0
RELIABILITY_GATE=PASS
PR46_READY_TO_MERGE=YES_AFTER_REPORT_CI
READY_FOR_M3_PRODUCTION=NO
```

The reliability gate passes for this bounded experiment, but production M3
remains OFF. The next authorized sequence is: merge the report-backed PR,
deploy merged main in M3-off mode, verify Gemini-only regression, then run the
previously planned 10-minute Gemini-versus-thinking-disabled-M3 A/B. Full
long-course A/B remains blocked until that A/B passes.
