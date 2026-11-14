# Golden V2 PR #190: Auto-rescue hardening and deployment gate

Status: CODE REVIEW / OFFLINE TEST ONLY. No production deployment or paid-provider dispatch is authorized by this document.

## Baseline

- Base PR: #190, commit 1ef99b4e4eb7cfcbf1ce9a4423b6c34bcf0663ea.
- Gemini primary remains REVIEW_REQUIRED when a known SAFETY refusal occurs. Its original provider evidence is immutable.
- Changes are limited to source-bound comparison, local orchestration, fail-closed launch controls, an opt-in Compose overlay and offline tests.

## Fixes and invariants

1. Original Chirp word indices remain canonical. A strict optional word-index mapping ensures comparison and adjudication use the same full-stream evidence IDs.
2. Each successful Gemini window is compared only to its corresponding Chirp time interval. Overlapping Gemini responses are never treated as a clean single transcript. Every seam is a bounded GEMINI_OVERLAP_SEAM_REVIEW candidate, never an automatic correction.
3. Refusal-only intervals exclude audio already covered by a successful Gemini window. Refused spans with no Chirp words are explicit review items, not confirmed silence.
4. QUEUED/PROCESSING jobs retain reserved worker capacity. NEEDS_REVIEW jobs have a separate bounded newest-first scan.
5. Verified partial-mode compare/adjudication/golden_qa claims may be recovered after a crash; original Gemini hold is never silently completed.
6. Paid executor recognizes an already-RUNNING independent Chirp rescue with durable resumption evidence and polls previously submitted operations. It does not issue an ordinary submit on that branch.
7. Auto-rescue hook uses a cross-process lock and fsynced durable launch intent before process startup. Ambiguous launches require operator reconciliation, rather than automatic retry after a cooldown.
8. The CLI requires --enable-auto-rescue before any paid dispatch; --dry-run only reads eligibility. The new Compose overlay uses the inactive-by-default v2-auto-rescue profile.

## Offline test evidence

Network-disabled, read-only-source container tests against the existing runtime image:

- 100 relevant Golden V2 unittest cases passed.
- Covered refused middle windows, overlaps, word indices, empty Chirp spans, queue fairness, stale downstream claims, paid GET recovery, concurrent hook invocation, interrupted hook startup, and read-only preview.
- Full repository unittest discovery exceeded the 100-second execution bound and must NOT be reported as passed. Re-run with targeted infrastructure or increase diagnostics before deployment.
- Docker Compose parser validates the new overlay; without the optional profile the service is not active.

## Mandatory release/owner gates

- Recheck GitHub CI and Codex feedback at the exact updated head SHA before merge.
- Historical Compose config-hash remains unmatched. Complete the five-service baseline verification, confirm image/command/env/mount/network/health/restart equivalence and document any signed alternative acceptance. The new overlay is not a replacement for the five-service Compose.
- Prepare immutable backup and rehearse a rollback using an isolated test copy; do not disturb production SQLite, audio, source Docs, or paid provider operations.
- Verify source identity, current owner grant, job-scoped and global caps, provider pricing and existing operation receipts before any paid submission.
- A SUBMITTING/unknown provider outcome requires reconciliation; never issue a second Speech POST on uncertainty.
- Unresolved acoustic coverage, overlap seams, word timing or names remain REVIEW_ONLY. Do not claim GOLDEN PASS from text matching alone.
- Enable the paid hook and deploy only after the user separately approves the release.

## Current scope

Only the isolated VPS development checkout was changed. Production containers, live Ledger, existing approved job, paid APIs, source audio and original Google Docs were not touched.

## Post-review hardening: Codex review of 6bd8238

Codex's second review identified two additional defects. A regression test
reproduced each failure before the code change:

1. **Offset gate across missing Gemini windows**: a refused prefix shifts the
   combined Chirp character positions, creating a false
   systematic_reference_offset even when each successful Gemini window
   aligns normally. Each successful window is now checked in its own local
   character coordinate system *before* evidence candidates are rebased for
   the report. Any genuine window-local systematic failure remains a BLOCKED
   report, and whole-audio coverage remains independently checked. The
   combined report includes window_alignment_health and
   offset_scope=WINDOW_LOCAL; no aggregate offset heuristic is applied to
   disjoint coordinate systems.
2. **Job approval and global cap conflation**: the auto-rescue hook must now
   receive an explicit independent --aggregate-cap-cents setting, not infer
   a global budget from any single job's approved_cents. A read-only global
   budget/unknown-paid preflight occurs *before* persisting launch intent.
   The actual paid claim still rechecks source, authorization and the global
   budget atomically. The opt-in overlay uses
   V2_CHAT_AUTO_RESCUE_AGGREGATE_CAP_CENTS, failing closed with 0 if unset.

A further regression test exposed an important unsafe retry: after a rescued
Chirp has completed and downstream adjudication places the job into
NEEDS_REVIEW again, the previous eligibility check could mark the held Gemini
as eligible for a new rescue. The eligibility check now requires *every*
downstream stage to be PENDING and no recorded independent rescue. Previous
Chirp operations and outcome-unknown provider states remain hard blockers.

The hook launches at most one new rescue per cycle, checks outstanding
launches in its durable state before attempting another job, and allows a
different job to proceed once the prior Chirp has evidence-bound COMPLETE
status and the global paid budget still permits the new attempt. Unknown
launches never automatically retry.

### Validation of this revision

- 106 focused Golden V2 tests passed in an offline network-disabled container
  with source mounted read-only, no model credentials, no production Ledger.
- New tests demonstrate:
  - A 600+ character false cross-window offset is accepted only when
    **window-local** comparisons pass.
  - A real 600-character local offset still BLOCKS.
  - Multiple approved jobs are checked against the aggregate cap without
    consuming a paid attempt on predictable budget rejection.
  - Missing global cap fails before any lock or intent is created.
  - Already-rescued jobs cannot be reclassified as new paid candidates.
  - Subsequent jobs remain blocked until the prior Chirp completes.
- A default profile Compose config does not activate the rescue service;
  opting into v2-auto-rescue only parses the service and is not deployment.
- CI, Codex third review, historical five-service Compose reconstruction,
  actual rollback rehearsal, and owner-deployment approval remain pending
  at the moment this addendum was written.

The historical job-scoped approval (for example 163 USD cents for the
Liu Yunzhong recording) **does not create or authorize a separate global
spending cap**. A deployment operator must provide an independently approved
global cap without exceeding any per-job approved limit or initiating a
second provider submission.
