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

Do not use Gemini 2.5. Use Google Vertex AI Gemini 3.7 Flash only for
text-only correction. Preserve raw results. Do not create public links, change
IAM/firewall settings, or upload to Drive without explicit user authorization.

Never reset/clean/stash an active dirty WebCodex/runtime worktree merely to make
it match `main`. Use an isolated branch/worktree for unrelated work.
