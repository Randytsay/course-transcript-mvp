# Golden V2 — Chirp local-clock repair handoff (2026-10-15)

This branch holds an isolated development fix, NOT a production deployment, provider completion, paid authorization, human listening or Drive publication.

## Source and billing safeguards
- Old job v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be has 6 DONE and 4 REVIEW_REQUIRED Chirp windows. Unreconciled paid stage blocks unrelated approved jobs.
- New approved 251018_.MP3 job is QUEUED with USD10 owner cap; do not bypass global ledger blocker or redispatch ambiguous provider requests.
- Preserve original provider raw bytes, result SHA, provider operation, actual billed cents and immutable review evidence.
- The safe parser coalesces adjacent same-start tokens only when later provider text supplies a valid provider end. No arbitrary 250ms word-timing guesses.
- All unresolved words remain explicitly flagged and block the automatic DONE transition. This is intentional. Some real windows may require actual human audio review.
- Golden merge verifies the fix SHA and reconstructs words from the raw source, instead of rerunning strict parsing that rejected it.
- A string owner reference is NOT an authentication mechanism. A future production endpoint needs its own authenticated owner/operator grant before apply.

## Responsibilities
ChatGPT: code patch, focused and regression tests, GitHub PR, CI, QA/integrity checklist, docs. Do not modify the existing Hermes worktree.

Hermes (VPS-only, read-only until release approval): inspect actual GCS provider receipts and operations, validate real source/version, report which of 106 timing anomalies can be corrected with verified same-start evidence and which require human listening, back up/verify live ledger, independently verify deployment identity and permissions, later deploy/canary/rollback using privileged operator with safe runbook, verify usage and original Drive outputs. No new Speech/Gemini calls, GCS writes, live SQLite edits, or claims of human listening during diagnosis.

## Release gates
1. Test all new safe fixes and unsafe holds; preserve original Chirp/provider/budget regressions.
2. Verify no lost words, no fabricated precise timestamps, no changes to original evidence or charges.
3. Enforce real source-specific authorization and immutable evidence for any ledger status transition.
4. New approved job proceeds only when genuine old paid outcome reconciliation clears global hold.
5. Golden QA and any necessary real listening, explicit publication grant, and Drive ID/hash read-back precede completion claim.

## New full-stage/global-blocker regression

A 10-window fake Chirp test was added after the first PR revision. It submits 10 fake local operation identities with 6 valid windows and 4 quarantined windows; repairs only four using provider-confirmed end boundaries; finishes via the ordinary Chirp recovery worker to change `chirp_independent` from RUNNING to COMPLETE; and verifies that a second approved job then transitions to PROCESSING through the real guarded `claim_next`. It checks that the six original window SHAs and their billed cents are unchanged and that only the first 10 fake Speech POSTs occurred. This proves the code path on synthetic evidence, **not** that all four real-world windows can be promoted.

## Explicit schema migration before production release

`GoldenV2Ledger.open_existing()` intentionally does not migrate a production ledger. The original PR would raise a missing-column error when attempting to update `local_clock_fix_sha` against the deployed ledger.

Added `scripts/golden_v2_migrate_clock_fix_schema.py` with dry-run default and an explicit `--apply --backup-dir` path. It requires an existing non-symlink absolute ledger path; takes an **exclusive backup** verified by SQLite integrity and row digests; performs an atomic ADD COLUMN under BEGIN IMMEDIATE; verifies job/stage/window record digests and recorded Chirp charges unchanged; never updates statuses or calls providers. It refuses to overwrite a prior snapshot. Tests verify dry-run, preservation and backup collision.

The authorized release operator must first stop the paid executor and confirm it is quiescent. Run the migration dry-run against the exact mounted production ledger from the candidate version, then make the exclusive backup and run --apply. Do not manually ALTER TABLE, use direct SQL status edits, or migrate via a credentialed API call. Rehearse the binary rollback against a **copy** of the migrated DB. Keep the additive migrated DB when reverting code; never restore an old SQLite snapshot over newer paid receipts. If a snapshot restore is ever needed, it requires a complete reconciled outage plan and preservation of all subsequent payments/operations.

**Do not run blanket `docker system prune`.** With 14GB free and other unrelated apps on the VPS, use read-only disk inventories first, then delete only proven unused build assets after owner review; retain old release images for rollback.

### Deployment gating

- CI green on the **new head commit**, not only on `36e850a`.
- A precise live-read-only evaluation of each actual quarantined window with the final parser; Hermes' previous estimate of "98 safe" relied on a looser fill rule and is not evidence that 98 are automatically safe with strict same-start coalescing.
- Actual old stage must only reach COMPLETE on independently verified full-audio evidence (not invented clocks); real human review may be necessary, even though the synthetic 10-window test is green.
- Per-job authorization, global aggregate exposure and Golden QA remain separate gates; delivery worker RUNNING never substitutes for publication grant.
