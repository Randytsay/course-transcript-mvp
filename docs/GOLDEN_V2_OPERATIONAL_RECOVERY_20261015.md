# Golden V2 — 2026-10-15 read-only operational recovery checkpoint

## Scope and rule
This is an operational handoff, **not** proof of release deployment, provider completion, approval, listening or Drive publication. The private ChatGPT plugin was updated to v0.2.0 to avoid repeat confirmations. The plugin cannot grant financial authority by itself. Never turn a technical outage into an owner approval question.

## Actual observed state
- Private V2 release reported `9506e4f714f46fa470bdaf077265199e94f1184f`.
- `golden-v2-chat-api-1` healthy; intake worker heartbeat running.
- `golden-v2-chat-paid-1` exited with code 0; its last observed pass returned `PrimaryTranscriptError` on one already-approved job. A clean exit does **not** prove reconciliation or safe restart. The API flag for paid execution is set, but service capability is false due to stopped worker.
- Delivery heartbeat unverified and publication API feature flag disabled; no `owner-publish-grant.json` exists in either observed V2 job directory.
- V1 is an independent deployment. Do not restart, patch, or invoke it as a workaround.

## Existing authorized job — do NOT redispatch
- `v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be`, source SHA `2069df0d67444a30e5243b27236c8f387292e9776e4e5eda65aca80c7f71a9fb`, original source `251025_.MP3`.
- Approved 210 cents, 24 cents of stage usage recorded at the time checked; Gemini `REVIEW_REQUIRED`; Chirp stage `RUNNING`, with compare/QA still pending.
- Read-only `inspect_paid_readiness` blockers: `EXISTING_PAID_RECEIPTS_RECONCILE`, `NOT_A_PENDING_PAID_STAGE`, `PAID_OUTCOME_HOLD_RECONCILE`. Provider calls allowed = false.
- `review` reports `PROVIDER_RESPONSE_REVIEW`, `human_listening_required=true`. Do not synthesize `confirm_listened`, replay Gemini, or overwrite prior raw receipt/operations. Reconcile receipts and the Chirp provider operation first using audited, GET-only mechanisms.

## User-requested job — no provider call started
- Drive folder: `1zZxjwfM--sj6IGaRn8ARlaq5nT8apnAq`; file `251018_.MP3`, Drive id `169Q08QUQ2jtPfRTZqxkHhmNzidHRgWIz`, version `9`.
- Trusted quote `v2q_da59bffa34314ca79e131d1dccf153ec`: `COMPLETE`, duration 13254.086531 sec; mode `FLASH_CHIRP`, conservative reservation 274 cents (Gemini 200; Chirp 74; storage/network excluded).
- Job `v2p_560465ab6db05e3da593837f4d76c630`; source SHA `93150c195c9493705d77a2820e44677d7bf06c5530eb4aa87328bd1a0365c7c6`, `AWAITING_APPROVAL`.
- Read-only readiness blockers: `BUDGET_APPROVAL_NOT_VERIFIED`, `OWNER_APPROVAL_NOT_ACTIVE`. Do not treat verbal willingness to pay as an approval of an *unspecified* numerical ceiling. No paid dispatch and no Drive write occurred in the observed quote.
- The existing 210-cent grant for the other source **must not** be reused for this job.

## Source changes in Draft PR #144
- API `service-status` now adds `capability_blockers` with independent API-feature and worker-health causes. This changes only diagnostics; no worker starts or authorization is inferred.
- An authenticated, read-only `GET /api/v1/golden-v2/jobs/{job_id}/paid-readiness` invokes the existing `inspect_paid_readiness` guard. Corresponding scoped client action: `paid-readiness <job_id>`.
- Tests added for blockers, authentication, read-only behavior, and invalid job ID. Offline test via network-disabled disposable V2 image: **11/11 passed**. PR CI and production deployment are separate gates.

## Next engineering steps, in order
1. Re-observe quote, job revisions and heartbeats. Inspect saved receipts/provider operations without retrying unknown outcomes. An `outcome_unknown` is not failure-to-submit evidence.
2. Reconcile the existing paid job under current provider and immutable-raw audit constraints. If truly human-dependent, leave it held and present a single precise listening packet.
3. Complete PR tests/CI; production cutover requires exact commit, verified backups, shared data and invariant checks, explicit deployment authority, and real worker heartbeat observation. Do not edit secrets or start live model generation to test health.
4. Resume a guarded paid worker **only after** reconciling active receipts/leases and verifying a real numerical, source-scoped grant. Provider runner must independently recheck current Drive revision, estimate/cap, quote SHA and operation identity before any dispatch.
5. Enable delivery only with the publisher contract and explicit grant for the exact SRT/TXT after Golden QA and truthful listening confirmation. Verify Drive file IDs and read-back hashes; never silently overwrite.
6. For reusable delegated budgets, implement owner-authenticated, period-bounded policy storage, atomic amount reservations and revocation/audit BEFORE treating one consent as authorization for future files. Default to OFF. No global $10 cap or chat memory may substitute for such a grant.

The acceptance criterion remains verified SRT + TXT in the original folder with actual dual-model provenance, corrected text agreement, Golden QA and delivery evidence. `PROCESSING` or a Git commit is **not** completion.
