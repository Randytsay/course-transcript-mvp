# Agent instructions

Before implementation, read in this order:

1. `CURRENT_STATE.md`
2. `DECISIONS.md`
3. `KNOWN_ISSUES.md`
4. `GLOBAL_GUARDRAILS.md`
5. `AI_AGENT_HANDOFF.md`
6. `docs/KNOWLEDGE_INDEX.md`
7. `ARCHITECTURE.md`, `RUNBOOK.md`, and the task-specific implementation docs

Repository/chat precedence is defined in `docs/KNOWLEDGE_INDEX.md`. Do not treat historical handoff files or chat history as newer than verified code/current knowledge files.

Do not use Gemini 2.5. **V2 new-development target is Vertex AI `gemini-3.8-flash`**; do not introduce new `gemini-3.7-flash` dependencies. V1 historical 3.7 evidence/runtime may still exist: preserve genuine raw model IDs and do not claim production has migrated until code, tests and exact-SHA deployment confirm it. The V2 design and acceptance contract are `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md` and `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`; record verified milestone results in `docs/GOLDEN_V2_GATE_STATUS.md`; their content is future specification, not evidence of a deployed implementation. Preserve raw results. Do not create public links, change IAM/firewall settings, trigger paid model calls, or upload to Drive without the applicable explicit user authorization.

Never reset/clean/stash an active dirty WebCodex/runtime worktree merely to make
it match `main`. Use an isolated branch/worktree for unrelated work.
