# M3 Production Readiness — 2026-08-16

## Decision

`READY_FOR_M3_PRODUCTION=NO`

The 24-gate production cutover authorization was not exercised. The default
route remains `GEMINI_FIRST`; no merge, production deploy, Drive publish, or
M3-default change was performed from this audit.

## Evidence completed

- Python regression: `191/191` passed.
- Frontend behavior tests: `12/12` passed.
- Next.js production build: passed.
- Local Compose and release Compose render/contract checks: passed.
- ARM64 Docker build of the isolated candidate image: passed.
- Gemini-only real smoke: a 3,369-segment course completed with
  `gemini-3.7-flash`, 42 windows, and zero raw fallbacks.
- MiniMax real sanity: 90 real segments completed with
  `initial_provider=minimax-m3`, `terminology_provider=minimax-m3`, 39 terms,
  90/90 M3 segments, zero provider switches, and zero content-guard fallbacks.
- The M3 sanity preserved exact segment ID order and exact start/end
  timestamps.
- Artifact credential scans: `0` markers for isolated A/B evidence and the
  prior production cutover evidence.
- Production readback before rollback: API/frontend healthy at release
  `049486a3234a37ae5d022eeb1229987c1250dd6f`; `default_policy=GEMINI_FIRST`;
  live quota state `available`; TWD budget readback `1200.00` remaining.
- Because that deployed image did not contain the parser fix, the two M3
  flags were rolled back to `false` with a protected `.env` backup. After
  restart, API health was `ok`, API/frontend HTTP was `200`, and API/worker/
  pipeline-worker readback showed both flags `false`.

## Gate blockers

1. Full-course M3 A/B did not complete on the candidate path. Before the
   parser fix, the 3,369-segment M3 run encountered a fenced-JSON parsing
   failure after bounded retries and correctly switched once to Gemini for the
   remainder. That proves fail-safe completion, not M3 quality readiness.
2. The M3 90-segment sanity differed materially from the Gemini baseline:
   M3 changed `34/90` segments and reported uncertain terms on `22/90`; Gemini
   changed `71/90` and reported uncertain terms on `6/90`. Exact corrected-text
   agreement was `33/90`. M3 latency was `171002 ms` versus Gemini `59154 ms`.
   These figures require human review; they cannot be declared an acceptable
   quality regression automatically.
3. Human edit burden and terminology accuracy were not independently reviewed
   against approved course edits, so gates 21–23 remain incomplete.
4. The deployed production image is the earlier release and does not contain
   the parser/chunking fixes from this audit. M3 is now disabled in production
   until a separate reviewable release passes the remaining gates.

## Root cause and preventive changes

MiniMax returned `<think>...</think>` followed by a fenced JSON block. The
previous parser removed only an outer fence, so the inner ````json marker made
otherwise valid structured output fail three times. The candidate now removes
reasoning wrappers and fenced markers in either order, retries invalid
structured responses within a bounded budget, and records the final raw
provider response in the redacted audit.

Long-course terminology extraction also now uses bounded chunks with per-chunk
audits and deterministic merge, rather than sending the entire course in one
request. Any quota, authentication, transport, or structured failure keeps
the one-way M3-to-Gemini switch and never re-enters M3 within that source job.

## Required next review before any M3 production enablement

- Repeat the full long-course M3 A/B with the fixed parser and bounded windows.
- Review terminology accuracy, missing content, semantic drift, uncertain-term
  rate, content-guard rate, latency, token-plan usage, and human edits.
- Complete controlled quota exhaustion across two consecutive source jobs.
- Complete staging service-level M3_FIRST E2E and rollback rehearsal.
- Only then set `READY_FOR_M3_PRODUCTION=YES`, create the reviewable release,
  and deploy.
