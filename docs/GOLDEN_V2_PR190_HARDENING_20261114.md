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
