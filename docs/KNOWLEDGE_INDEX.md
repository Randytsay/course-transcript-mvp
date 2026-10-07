# Knowledge Index

This index tells future Agents which documents are current contracts versus historical evidence.

## Start here — current knowledge

- `CURRENT_STATE.md` — canonical repository / branch / in-flight state.
- `DECISIONS.md` — durable project decisions.
- `KNOWN_ISSUES.md` — active risks and drift.
- `GLOBAL_GUARDRAILS.md` — cross-project rules inherited from Randy AI Knowledge Brain.
- `AI_AGENT_HANDOFF.md` — safe takeover procedure.
- `RUNBOOK.md` — operational safety and production commands.
- `ARCHITECTURE.md` — runtime / pipeline architecture.
- `skills/golden-course-transcript/SKILL.md` — Golden workflow operator contract on the current branch.
- `docs/GOLDEN_COURSE_TRANSCRIPT_WORKFLOW.md` — stable Golden workflow pointer.

## Current implementation contracts

Use as applicable:

- `docs/STATE_MACHINE.md`
- `docs/DATABASE_SCHEMA.md`
- `docs/API.md`
- `docs/VPS_DEPLOY_GATE.md`
- `docs/PRODUCTION_CUTOVER.md`
- `docs/ASR_RETRANSCRIPTION_LIVE_GATE.md`
- `docs/HUMAN_REVIEW_DELIVERY_GATE.md`
- `docs/drive-api-deployment.md`
- `docs/LEARNING_PLATFORM_AI_ARTIFACTS.md`
- `docs/LEARNING_PLATFORM_RELEASE_CANDIDATE.md`

## Historical / provenance-only documents

A document that explicitly labels itself historical must remain available for audit but must not override the current entry-point files.

Examples include:

- `docs/DEPLOYMENT_STATUS.md` — historical 2026-07-31 snapshot.
- numbered/phase-specific validation records under `docs/`.
- old PR-specific handoffs after their branch was merged or superseded.

## Precedence when documents disagree

1. Actual audio / immutable provider evidence.
2. Current code + tests on the exact target commit.
3. Current root knowledge files and current Runbook/Architecture.
4. Current Skill/workflow contract.
5. Branch-specific handoff.
6. Historical validation/provenance docs.
7. Chat history.

Chat may reveal a decision that still needs migration, but it is not the final engineering source of truth until written into version control.
