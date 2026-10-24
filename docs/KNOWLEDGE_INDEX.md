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

## Golden V2 — current authoritative documentation

- [Current architecture](GOLDEN_V2_CURRENT_ARCHITECTURE.md) — exact-SHA
  deployed runtime, implementation map, verified current blockers.
- [Independent external audit](GOLDEN_V2_INDEPENDENT_AUDIT.md) — directly
  reusable instructions to investigate why four tasks cannot reach Golden QA.
- [Acceptance contract](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md) — retained
  G0–G8 standards, not proof any particular task passed.
- [Historical gate diary](GOLDEN_V2_GATE_STATUS.md) — preserve dated evidence;
  not the live status database.

The original 2026-10-11 architecture plan and obsolete integration handoff
are removed from the active tree; their exact bytes remain accessible in
Git history at 17d33faf6239ca5a1fcdfe91f18cef202f271b0f.
Do not let historical drafts override current exact-sha code or runtime.

## 2026-10-13 — Active P5 QA handoff

- `docs/GOLDEN_V2_P5_CHATGPT_WEBCODEX_QA_2026-10-13.md` — owner decisions, supervised ChatGPT/WebCodex model roles, first **real four-minute** offline P5 rehearsal, evidence-bound blockers, and next integration gates. This is a development checkpoint, not production Golden PASS.
- `app/pipeline/golden_v2_p5_evidence_trial.py` — provider-free create-only review candidate with immutable receipt/owner/SRT validation; no live paid models.

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
