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

## Real evidence preview without any model/GCS call

Added `scripts/golden_v2_clock_fix_preview.py` to inspect the already-saved `provider-raw.json` for each `REVIEW_REQUIRED` window. It opens SQLite in read-only mode, confirms raw SHA equals the immutable recorded result, verifies the review packet's job/source/window/operation and billed-cents binding, then executes the exact production-intended strict parser in memory. It emits **counts only**, no recognized text. `safe_to_apply` is false if any word lacks provider-verified timing. This preview is not an approval and does not write a proposal or switch any ledger status.

Recommended isolated VPS sequence after fetching the latest PR head (not from the stale Hermes worktree): run a new container from the existing V2 Python image with `--network none --read-only`, mount the PR code at `/app` read-only and the production V2 data path at `/app/data` read-only, and execute `python -m scripts.golden_v2_clock_fix_preview --database /app/data/golden-v2/ledger.sqlite --artifact-root /app/data/golden-v2 --job-id v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be`. Redact private paths and do not export speech text. Compare real `safe_windows` and `requires_attention`; do not infer from Hermes' looser 98 repairable candidates that all four real windows satisfy the strict provider-boundary policy.

The new full synthetic test proves stage COMPLETE and that global ledger paid hold may safely clear **only when all ten windows have verified results**. It cannot waive real-world human review or automatically terminate a paid stage whose evidence is incomplete.

## 2026-10-15 production receipt preview: fail-closed result

The candidate read-only offline preview was actually executed with both PR code and the existing V2 data mounted **read-only**, networking disabled, and no credentials or provider calls. It verified each quarantined raw provider receipt against its ledger SHA and did not mutate SQLite/GCS/Drive. Latest strict sorted-by-provider-timestamp parser results:

| Window | Provider word entries | Coalesced token count | Unresolved evidence items (not distinct words) | Reason |
| --- | ---: | ---: | ---: | --- |
| chunk-0005 | 3,052 | 2 | 1 | source-boundary overflow |
| chunk-0007 | 2,818 | 0 | 6 | out-of-order provider tokens + five timing overlaps |
| chunk-0008 | 2,922 | 187 | 2 | out-of-order provider token + one timing overlap |
| chunk-0009 | 2,860 | 4 | 3 | missing verified end + order conflict + overlap |

Result: **0/4 windows can be promoted automatically** under the evidence policy. These counts are not errors in the original spoken words or proof that an entire 900-second window needs re-recognition. The prior non-sorted parser had produced many cascading false overlap flags, which were fixed in the current PR by sorting only raw provider-supplied timestamps and flagging any lexical order conflict for review.

The next actual VPS task is to produce a **redacted, exact source-relative time-range review packet** for these few boundary/ordering conflicts, without uploading, re-recognizing, inventing timestamps or mutating provider receipts. Determine whether a small authenticated listening review can establish a valid timing correction for each. **Do not run apply_local_clock_fix on these real four windows** until unresolved evidence has been adjudicated through a separate, auditable workflow. Synthetic stage-completion tests cannot replace this gate.

The paid executor processes **all active jobs** in its ledger, not just the new 251018_.MP3 job; canary rollout must hold all paid dispatch safely, not assume it affects one job.
