# AI Agent Handoff

## Read first

1. `AGENTS.md`
2. `CURRENT_STATE.md`
3. `DECISIONS.md`
4. `KNOWN_ISSUES.md`
5. `GLOBAL_GUARDRAILS.md`
6. `docs/KNOWLEDGE_INDEX.md`
7. Relevant implementation docs / tests for the task

## Project goal

Turn course audio/video into evidence-grounded, resumable Golden subtitles/transcripts, safely publish audited sidecars, and provide version-grounded learning artifacts without losing provider evidence or repeating expensive work unnecessarily.

## Non-negotiable invariants

- Audio reality outranks references.
- Chirp word timing is timing truth.
- LLMs correct text; they do not invent timestamps.
- Completeness precedes semantic correction.
- A completed pipeline is not automatically Golden.
- Preserve successful chunks; repair only verified missing/broken bounded regions.
- Never retry a dead provider operation as if it were pending.
- Source media and raw provider evidence remain immutable.
- Drive delivery retry does not rerun upstream paid work.
- Production release comes from an immutable exact SHA, never a dirty worktree.
- Secrets never enter Git, logs, manifests or chat.

## Current repository caveat

As of 2026-10-07:

- `main` observed at `c632064...`.
- `feat/media-inventory-dedup` contains accepted newer Golden policy but is divergent from main.
- the active WebCodex working copy on that feature line is dirty with additional uncommitted work.

Before modifying repair/lineage/Golden code, inspect current branch/worktree status and do not overwrite active work.

## Safe task pattern

1. Establish exact branch/commit and dirty status.
2. Identify whether the requested behavior is main, branch-only, or uncommitted.
3. Reuse existing provider evidence.
4. Prefer local/provider-free verification before a paid call.
5. For paid retry, prove the exact missing interval and budget authorization.
6. Run focused tests.
7. Run the relevant full/CI validation.
8. Record decision/incident changes in project knowledge.
9. If a lesson is cross-project, promote it through `AI_Knowledge_Brain`.

## Handoff output

Always report:

- exact commit/branch;
- whether the worktree was dirty;
- whether any paid provider call occurred;
- whether Drive was mutated;
- evidence reused vs regenerated;
- QA / completeness verdict;
- remaining review items;
- production modified: YES/NO.
