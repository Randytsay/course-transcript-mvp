# Golden V2 — Gemini-first full context and new Chirp dispatch gate (2026-10-15)

## Actual evidence, not inferred work

The production old job `v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be` is still in Gemini `REVIEW_REQUIRED` and Chirp `RUNNING`; new `251018_.MP3` job is approved/queued but globally blocked. Strict offline, read-only inspection of the old primary report confirms 4 planned Gemini 3.8 Flash audio windows, only 2 raw response receipts, first `COMPLETE` (9,058 characters), second `REQUIRES_REVIEW` (0 characters), remaining windows without saved responses. `structural_coverage=INCOMPLETE`; stage `ready_for_chirp=false`. Do not assert that Gemini already transcribed the whole 148-minute source.

## Fix implemented in PR #145

1. `require_complete_gemini_before_chirp()` reads the real ledger, immutable source lineage, SHA-verified normalized FLAC, exact primary stage or authenticated reviewed-seam evidence, and *every* saved Gemini raw window audit. It re-runs structural coverage/merge validation in memory, including final audio tail, source-identity, complete provider finish reasons and original seam decisions. It fails closed if anything is incomplete or tampered.
2. `execute_trusted_paid_stage()` now invokes this verification **before the Chirp stage CAS claim and any newly billable Chirp provider submission**. It does NOT affect GET-only recovery of existing paid Chirp operations, which must not be replayed or discarded.
3. Gemini primary runs sequential source-overlapping audio windows. A following window can receive the final bounded 500 characters of the prior **provider-completed** window as lexical/seam context; instructions explicitly forbid treating this as speech in the current audio or as a command. This does not create a "full transcript" until all windows pass the independent coverage checks. The exact context is included in the paid window identity. The physical paid audio slot digest deliberately ignores mutable context, so old receipts with different context cannot be silently resubmitted or charged again.
4. `golden_v2_primary_context_preview.py` outputs **only** provider response statuses, saved/planned counts, and proof flags. It never exports private transcript text, calls Gemini/Chirp, or mutates a ledger.
5. Added regressions for partial Gemini, exact source and immutable raw audit evidence, reviewed seams, context handoff and no second paid call after prompt/context change.

## Operational plan / safety

- **Do not run Gemini calls just to fill a status gap.** First reconcile the old paid response and any ambiguous provider outcome against immutable receipts, source/revision/cost and the old US$2.10 limited quote. The user's new US$10 source-specific consent is **not** an extension to the old job.
- Full-context readiness establishes *structural source coverage*, not independent acoustic completeness. Gemini recognizer may skip audible speech despite STOP; Chirp independent evidence and Golden QA are still required.
- Existing four Chirp `REVIEW_REQUIRED` windows remain unchanged; previous paid results and actual cents preserved. User reports all four short clips sound normal; this is helpful human feedback but not a machine-verifiable per-word timestamp correction receipt.
- No live worker changes, Drive writes, source modifications, paid provider requests, or SQLite changes as part of this PR's tests/diagnostics.
- Do not turn Draft PR ready or deploy until independent CI, new version canary, release schema migration, source-bound manual clock review and rollback plan are ready. Version rollback must not overwrite the live ledger with old backups.
