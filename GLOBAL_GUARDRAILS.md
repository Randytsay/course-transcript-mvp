# Global Guardrails

Source: `Randytsay/AI_Knowledge_Brain`

These are cross-project rules that apply to this Project. Read them together with local `DECISIONS.md` and `KNOWN_ISSUES.md`.

## RK-GRD-0001 — Bound external polling and retry

Chirp / external operations require finite deadlines, bounded recovery, terminal detection and durable operation identity. A terminal provider operation is evidence, not a pending job.

## RK-GRD-0002 — Preserve successful units; retry only bounded failures

Never rerun an entire course merely because one chunk/window failed. Reuse successful provider evidence and retry only the smallest verified failed/missing interval.

## RK-GRD-0003 — Resolve identity and lineage before costly processing

Historical media must pass duplicate/master/segment/alternate/derived lineage checks before paid ASR or knowledge ingestion.

## RK-GRD-0004 — Preserve authoritative fields from deterministic sources

LLMs must not invent/overwrite timing, hashes, IDs, amounts or release revisions. For subtitles, Chirp word evidence owns timing.

## RK-GRD-0005 — Retry side effects from materialized artifacts

Drive delivery failure retries existing validated artifacts. It must not rerun Chirp/semantic correction.

## RK-GRD-0006 — Settle cost/reservation ledgers on every terminal path

Success, failure, cancellation and retry must reconcile reserved budget against real recorded usage.

## RK-GRD-0007 — Guard high-frequency storage writes and quota

**Status: provisional / conditional applicability.**

The learning-platform portion of this broader Project experienced a write-quota exhaustion incident on 2026-10-06. High-frequency heartbeat/polling persistence must dedupe unchanged writes, bound rate, expose quota health and stop before exhaustion.

Do not claim the project-specific fix is fully verified until the exact code path and production evidence are recorded.

## RK-GRD-0008 — Deploy only immutable exact-SHA releases

Never deploy from the dirty WebCodex/live worktree. Build, validate, deploy and verify an immutable exact Git SHA.

## Reconciliation metadata

- Brain: `Randytsay/AI_Knowledge_Brain`
- Initial reconciliation: 2026-10-07
- Guardrail IDs: RK-GRD-0001 … RK-GRD-0008
