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
