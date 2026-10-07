# Incident / Lesson — Golden repair, terminal operations and cost reconciliation

Date range: 2026-10-04 to 2026-10-07

## What surfaced

Historical Course Transcript jobs contained a mixture of:

- successful chunks;
- old BLOCKED / needs-review results;
- provider terminal/no-output attempts;
- possible audible gaps and density anomalies;
- stale or leaked cost reservations;
- duplicate/master/segment/alternate media relationships;
- already-published outputs that could become stale after semantic cleanup.

The unsafe naive response would have been to rerun whole recordings, keep polling terminal operations, or treat every historical blocker as a current provider failure.

## Corrective direction

The project converged on:

1. provider-free local Coverage/VAD/QA before new paid calls;
2. reuse of successful chunks;
3. bounded Standard Batch repair for verified gaps only;
4. fresh attempt identity for terminal/no-output replacement repairs;
5. multi-round bounded repair instead of whole-job replay;
6. reservation settlement and budget revalidation;
7. local recheck from retained merged evidence without resubmitting ASR;
8. lineage/canonical identity before processing duplicate historical media;
9. strict Golden promotion separate from ordinary pipeline completion;
10. safe republish only when current artifact hashes differ from Drive.

## Evidence

Relevant merged/main fixes include:

- `3669c5e...` — settle terminal cost reservations.
- `8bb0028...` / `e81b11e...` — stale reservation reconciliation.
- `337b60c...` / `74cebc5...` — bounded repair resume / reconstructed validation lineage.
- `b88e1aa...` / `b94b827...` — Standard repair / Golden coverage policy line.
- `07be312...` / `8500dbe...` — terminal patch replacement safety.
- `64ee2af...` / `165a151...` — legacy completeness blocker replan.
- `c632064...` — preserve references and publish audited sidecars.

The separate `feat/media-inventory-dedup` branch adds newer canonical-first / lineage / local-fallback policy but is not equivalent to current main.

## Cross-project lessons promoted

- RK-GRD-0001 — bounded polling/retry
- RK-GRD-0002 — preserve successful units
- RK-GRD-0003 — identity/lineage before costly processing
- RK-GRD-0005 — retry side effects from materialized artifacts
- RK-GRD-0006 — settle cost ledgers
- RK-GRD-0008 — immutable exact-SHA deployment
