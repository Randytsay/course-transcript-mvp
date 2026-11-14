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

## Codex third review: per-job approval ceiling preflight

The 2bd5cba Codex review exposed one more predictable paid-claim
failure: a job approval can exceed an independently configured
aggregate cap even while the whole-ledger estimated exposure is
within that cap. The trusted claim rejects approved_cents > cap_cents.

Fixed: the rescue hook now checks the job's approved_cents against
the explicit aggregate cap before writing any launch intent. It
returns JOB_APPROVAL_EXCEEDS_AGGREGATE_CAP for the affected job
without consuming an attempt or blocking another eligible job.

Red/green regression: two held jobs, approvals 1000c and 343c,
combined ledger exposure 686c, configured aggregate cap 800c.
The 1000c job must be deferred without a launch record while the
343c job may proceed in a mocked, isolated process-spawn test.

107 focused Golden V2 offline tests passed in a network-disabled,
read-only-source container after this fix. This does not constitute
a production provider or deployment acceptance.

## Fourth Codex review: ordinary Paid Executor vs Auto Rescue race

Codex review at 278b75e found a new preclaim concurrency risk:
the auto-rescue hook used auto-rescue-hook.lock, whereas the normal
Paid Executor used paid-executor.lock. A paid stage claimed between
rescue's global preflight and the detached child claim could make the
rescue fail after a durable launch intent, blocking future rescue
attempts despite no rescue provider POST.

### Safety fix

- The hook now also acquires the SAME paid-executor.lock as the
  regular Paid Executor BEFORE its preflight and intent.
- If already held, the hook reports PAID_DISPATCH_BUSY without writing
  a launch attempt.
- On spawn, pass_fds transfers the identical locked file description
  to the rescue process. Parent FD closure cannot release the lock
  before child claim and provider submission status are persisted.
- The child validates that any inherited descriptor refers to the
  exact expected regular file (device/inode, no symlink). Standalone
  operator rescue also acquires the Paid Executor lock.
- The child closes the inherited lock only after source/owner verification,
  CAS rescue claim, and the initial planned provider submission path;
  GET-only recovery is unlocked before the long polling loop.
- On failures before this point the process exits and the durable hook
  intent remains fail-closed. The current fix prevents the normal paid
  executor from racing the rescue preclaim; it does NOT bypass an
  unresolved provider outcome or automatically relaunch it.

### New offline validation

- 109 targeted Golden V2 tests PASS in a network-disabled container.
- Added tests: a running normal paid lock defers rescue without intent;
  and a detached mock child inherits and retains the lock so the normal
  paid executor returns PAID_EXECUTOR_BUSY until child exit.
- No production service, paid provider, Drive original, or production
  Ledger was used for these tests.

### Separate isolated Compose audit

Restricted report is on the VPS at:
/srv/ai-workspace/projects/golden-v2-audit-20261114/GOLDEN_V2_COMPOSE_DIFF_AND_ROLLBACK_ACCEPTANCE_20261114.md

- Five-service isolated patched-code import/start/health and
  old-release rollback smoke test: PASS, staging fully cleaned.
- This is NOT complete production-equivalent semantic rollback.
- Source/live aggregate cap mismatch: 5000c in current Paid runtime
  vs 1000c in checked-in Paid fragment.
- Source/live delivery --lock-root path also differs.
- Historic config-hash still unmatched; five-service reconstruction
  must not be declared verified yet. Keep merge/deploy blocked.

### Additional inherited-FD spoofing defense

The child additionally attempts a nonblocking flock on the inherited
descriptor after verifying the correct device/inode. An unlocked descriptor
of the correct file cannot bypass a concurrent legitimate holder. The new
negative regression mocks a locked regular Paid Executor while passing an
unlocked descriptor of the same file; the script fails before initializing
any paid provider. 110 focused offline Golden V2 tests passed.
