# Current State

## 2026-10-13 — Operational private ChatGPT Skill entry (non-paid)

The owner requested first enabling direct ChatGPT invocation of 黃金字幕 V2.
A private `golden-subtitles-v2` plugin/skill v0.1.0 is saved in the owner's
ChatGPT account and uses the existing connected WebCodex VPS App. ChatGPT UI
shows one skill and the connected App; the plugin is selected in an unsent
status-check composer. Real WebCodex calls verified the deployed private V2
API, intake heartbeat, Drive GET connectivity and shared ledger query.

The independent `golden-v2-chat` API/intake deployment runs exact source
`d80d0fd9d4f13a43a22d9891dc491e5d4d1384fa` on Oracle ARM64 and binds only
`127.0.0.1:8816`. Local 1040 tests, image-contained 25 focused ARM64 tests,
exact-SHA CI, auth rejection and V2 restart/volume checks passed. V1 remained at
`adafd952b9b89db111730ab28e8e8baff9551021` and was not restarted. The new entry
supports source preview/intake/status only; paid execution, Golden delivery and
Drive publication remain disabled/unintegrated. No owner media was downloaded
or live batch submitted in this installation. See
[chat contract](docs/GOLDEN_V2_CHAT_ENTRY.md) and
[deployment evidence](docs/evidence/golden-v2-chat-entry-deployment-20261013.json).

## 2026-10-13 — Drive link intake and persistent controller integration (development)

New local source integration on PR #144's `7f6da90` baseline: bounded Drive
link discovery, shared-ledger durable intake batches, Next.js entry/proxies,
restartable non-paid importer, scoped operator CLI and disabled paid controller
using existing guarded dispatch/Chirp recovery. See
[checkpoint](docs/GOLDEN_V2_LINK_TO_RUN_2026-10-13.md) for implemented contracts,
verification and remaining runtime/QA/publication gates. Production remains V1
`adafd952b9b89db111730ab28e8e8baff9551021` at this turn's read-only check.
No provider call, Drive write or production activation is implied.

## 2026-10-13 — Latest P5 ChatGPT/WebCodex QA implementation checkpoint (development only)

- Draft PR #144 branch `codex/golden-v2-goal-completion`: new owner decision is 3.8 semantic primary + Chirp acoustic timing + optional Gemini 3.5 Preview for serious differences, under ChatGPT/WebCodex-supervised QA and a separately operated VPS durable worker. **No production default has changed.**
- Three actual independently run **four-minute** model receipts exist in immutable private GCS. The owner made 12/12 segment model-preference choices and confirmed 「所以比如有長行」. Previous GA 3.5 attempts remain `OUTCOME_UNKNOWN`; the successful Preview receipt is separate, not a relabelled old attempt.
- Newly tested **offline P5 review-only bridge**: `app/pipeline/golden_v2_p5_evidence_trial.py`, 10 focused unit tests PASS, live source/receipt/owner/cue check PASS. Five artifacts (candidate SRT/TXT, comparison, adjudication, QA report), 60 candidate cues, 710 Chirp acoustic words, 12 candidate pauses flagged as unchecked. `GOLDEN_PASS=false`, `REVIEW_REQUIRED`: VAD/full-audio audio check, word-level alignment and verified final Golden editing not yet done.
- All paid actions default OFF. Main/V1 runtime not changed; long-duration paid jobs, G0–G8 final acceptance and Drive publication remain pending. Evidence and continuation plan: [P5 checkpoint](docs/GOLDEN_V2_P5_CHATGPT_WEBCODEX_QA_2026-10-13.md).


Last reviewed: 2026-10-13 (P5 four-minute offline QA prototype; production SHA remains unverified)

## V2 work in an isolated Draft PR — not deployed

- [V2 architecture specification](docs/GOLDEN_V2_ARCHITECTURE_PLAN.md) separates Gemini `gemini-3.8-flash` **full-audio recognition** from Vertex Gemini `gemini-3.8-flash` **text-only correction** (independent adapter); Chirp 3 remains the independent recognizer/time authority, with optional Gemini 3.5 Transcribe and ChatGPT/WebCodex QA.
- Isolated Draft PR #144 has provider-free source and fake-provider integration for durable stages, bounded repair, acoustic QA, and immutable evidence. Its last verified CI SHA is recorded in `docs/GOLDEN_V2_GATE_STATUS.md`; G0–G8 remain incomplete and no production state is inferred from CI.
- [V2 development and acceptance gates](docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md) define P0–P8/G0–G8 milestones.
- This is an **implementation in a draft branch only**, not proof of production deployment, active job state, credentials, billing, or live model availability. Do not rewrite historic model provenance.
- Continue provider-free implementation and verification in the isolated branch. Paid provider calls, IAM/billing mutations, Drive publication, and production cutover remain outside scope without separate explicit authorization.


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
