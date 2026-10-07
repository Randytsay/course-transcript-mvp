# Current State

Last reviewed: 2026-10-07

This file is the entry point for the **current repository state**. It intentionally separates merged main, reviewed-but-unmerged work, and uncommitted runtime work. Do not infer production state from a development worktree.

## Canonical repository state

- Repository: `Randytsay/course-transcript-mvp`
- Canonical branch: `main`
- Implementation baseline before the knowledge-only migration: `47e546faa83b32aaae562e3a047955921ff23d69` (2026-10-07)
- Knowledge Migration baseline merge: `52f2a4ebf68c2ae1955ff89110dee7c40794ee47` (2026-10-07)
- Exact production SHA: **must be verified at runtime** with the release label / `runtime_revision_guard.py`. It is not inferred from this file.
- Golden timing truth: Chirp 3 word-level timestamps.
- LLM role: text/semantic correction only; it must not invent or alter provider timing.
- Drive source media: immutable. Derived sidecars use the resumable safe-publish transaction.

## Merged behavior on main

Main already contains, among other things:

- 2026-10-07 production dependency audit remediation: `source-map-js` pinned to 1.2.2 and `sharp` pinned to 0.35.5 after newly published advisories caused the previously-green baseline to fail CI.

- Golden Course Transcript skill / reference-completeness workflow baseline.
- Bounded targeted repair and safe terminal-patch handling.
- Cost-reservation settlement / stale-reservation reconciliation fixes.
- Preservation of human reference transcripts with separate corrected transcript/report sidecars.
- Fail-closed structural/content validation before publication.
- Exact-SHA deployment and runtime revision guardrails.
- Learning-platform evidence contracts in which stored AI artifacts are pinned to immutable subtitle versions and learner reads do not trigger paid generation.

## Reviewed policy / branch work not yet canonical main

A development branch `feat/media-inventory-dedup` was observed on 2026-10-07 at:

- head: `b224423ec34a111bbea78a48234aa2409a227bd2`
- merge base with main: `96f71cb585e1f539bcd6eeb10bbd2b5db838688a`
- status vs main at observation time: ahead 6 / behind 16

That branch contains the newer Golden workflow policy (v1.6/v1.7 family), including:

- provider-free media inventory / lineage before paid ASR;
- canonical-first processing;
- Dynamic Batch for bulk first-pass canonical media;
- Standard Batch for every retry/repair;
- local Coverage / VAD / QA as provider-free gates;
- bounded multi-round repair;
- no local recheck-triggered ASR resubmission;
- explicit local-ASR emergency fallback provenance;
- Golden Corpus allowlist and canonical-only knowledge ingestion.

These rules reflect the accepted direction, but **agents must not claim they are on main or production until branch reconciliation, tests, merge, and exact-SHA deployment evidence exist**.

## Uncommitted WebCodex work observed

The active WebCodex working copy was dirty on 2026-10-07. Observed uncommitted work included media inventory/lineage, Golden finalization, semantic repair, publication helpers, cost reconciliation and related tests/reports.

Do not:

- reset, clean, stash, overwrite, or bulk-format that worktree;
- assume those files are merged;
- use that dirty worktree as a production release source.

Use a separate branch/worktree for unrelated changes.

## Golden definition

`completed` is a pipeline state, not a Golden-quality label.

A transcript may be promoted to Golden only after the applicable evidence gates pass:

1. Acoustic/timeline completeness.
2. Duplicate/lineage disposition.
3. Domain terminology evidence.
4. Same-course presentation/material evidence where applicable.
5. Semantic review evidence.
6. Final structural/content QA.
7. Drive publication freshness.
8. Canonical-only knowledge-ingestion decision.

Historical `BLOCKED` / `needs_review` artifacts are audit history, not necessarily current truth. Revalidation must use current audio/evidence before deciding whether repair is still required.

## Next safe work

1. Reconcile `feat/media-inventory-dedup` with current `main` without touching the dirty runtime worktree.
2. Preserve all successful provider evidence and repair only verified audible gaps.
3. Finish provider-free local revalidation for historical blockers before authorizing any new paid repair.
4. Record the final Golden/canonical allowlist before second-brain ingestion.
5. Keep this file updated whenever main, production SHA, or the Golden contract changes.

See also:

- `DECISIONS.md`
- `KNOWN_ISSUES.md`
- `GLOBAL_GUARDRAILS.md`
- `AI_AGENT_HANDOFF.md`
- `docs/KNOWLEDGE_INDEX.md`
- `docs/GOLDEN_COURSE_TRANSCRIPT_WORKFLOW.md`
- `skills/golden-course-transcript/SKILL.md`
