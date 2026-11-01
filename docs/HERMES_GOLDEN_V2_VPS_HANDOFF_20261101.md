# Hermes Agent handoff — Golden Transcript V2

## Role and scope

You are the **VPS privileged operator / release engineer**, not an alternative source of speech truth and not an owner of new spending. ChatGPT/Codex prepared the independent `AUTO_ACCEPTED_WITH_REVIEW` API + publication bridge in `course-transcript-mvp`. Complete **only** operations genuinely requiring authorized VPS access, durable deployment infrastructure or persistent scheduling. **Do not rewrite the QA engine or duplicate its business rules.** Start in read-only mode.

## Source of truth

- Repo: `Randytsay/course-transcript-mvp`, PR stack #159 → #161 → #162 → #163 → the latest `auto-delivery-bridge` PR. Resolve exact commit SHA from GitHub, confirm CI and Release Compose; review before any merge/rollout.
- Runbook: `docs/GOLDEN_V2_AUTO_DELIVERY_ROLLOUT_20261101.md`; acceptance policy `docs/GOLDEN_V2_AUTO_ACCEPTANCE_TIER_20261101.md`.
- Golden V2 separate production: `/opt/course-transcript-v2-chat/`; last observed image `82e0cd45aa7ad80d776e2dcd0b0cfe5383eeede0`; containers `golden-v2-chat-api-1` & `-intake-1` running, `-paid-1` and `-delivery-1` stopped. Verify live, do not trust snapshot blindly.
- Protected original backup `/opt/course-transcript-v2-chat/backups/pr159-20261029T071713Z`; **never overwrite/recreate** this backup. The `webcodex` uid999 currently cannot read it. No chmod/sudo bypass; request approved root diagnostic path if needed.
- IMPORTANT `sudo webcodex-deploy course-transcript SHA` targets **V1**. **NEVER run it for Golden V2**.

## Execution orders, with stop gates

1. Audit (read-only) exact file/folder/Git SHA, compose labels, container health, root-owned V2 Ledger and operations, old backups & restorable artifacts, disk size/WAL writers; avoid printing tokens or raw student/teacher audio content. Report `GO/NO-GO` with evidence.
2. Confirm you have a root-approved, V2-scoped exact-SHA **deploy + rollback** command, or create one via standard privileged change-management review. **Never widen `sudoers`, folder ACLs or operator allowlists ad hoc**. If not authorized, stop and ask the owner/admin for the required privileged action. No workaround via Docker group.
3. Verify CI+Compose exact HEAD on *all* stacked PRs, code review and manifest SHA. Confirm no V1 changes. Use an approved dedicated V2 release directory, independent pinned image digest, and stage roll-forward with all paid/Drive writers OFF. Run restore drill to separate isolated path and prove SQLite integrity/row counts and source artifacts; do not restore over active DB/WAL.
4. Canary API+intake only, auto-delivery gate default-OFF, paid/golden delivery workers OFF. Check healthy endpoints, limited owner authentication and negative tests. After operator-approved feature activation, stage test candidate SRT/TXT in **bound** job `golden-v2/` folder, call `prepare`, then explicit source-bound owner `authorize-publication`, then `publish`, using the API contract in rollout runbook. Never sign approval for the owner by inference. Verify four Drive output files and source MD5/version under shared lock, SHA readback and idempotent receipt; no old files overwritten or renamed.
5. Once sandbox canary is green, separately handle historical lessons 20221217 (job `v2p_cce7a9147a495027bd16f5619ad17b8d`), 20221224 (`v2p_437c811bbf615f42bf54f49ad439465d`), 20221231 (`v2p_48d9edc699da5d8ebe020d4f0b6afa8f`). Import **exact** previously verified SRT/TXT candidates, not untrusted guesses; preserve review reports and record distinct quality tier. Original MP3 and prior editorial SRT/TXT must remain unchanged.
6. Do not submit new Chirp/Gemini paid jobs, replay old cloud LROs, auto-correct text without acoustic evidence or mark strict Golden PASS unless independent owner budget, provider accounting and required sound gates are satisfied. Current auto tier is allowed to complete with documented unknowns, but technical errors must block.
7. Configure durable default-off observation/monitoring only after approved: statuses `PREPARED`, `AUTO_DELIVERED`, `AUTO_BLOCKED_TECHNICAL`, `NEEDS_RECONCILIATION`; crash-resume intent recovery, rate limits, no duplicate Drive names. For full incoming-course automation, separately plan and test provider/semantic gaps and budget guardrails; do NOT turn on recurring paid inference from a general instruction.
8. If a release fails: stop rollout, preserve logs+receipts+original files, invoke **the approved V2-specific** rollback path, verify source/ledger integrity and report `NO-GO` with exact blocking evidence. Never delete unknown remote pending IDs or lose prior completed provider receipts.

## Required final operator report

- Result: `LIVE_GO` or `NO_GO` (do not label source code merged as deployed).
- Exact deployed commit/Image sha/digest, CI + Release Compose links, backup integrity and isolated restore evidence; access scope used.
- Actual API response/health and feature flag statuses (never secrets); original v2 ledger state and new independent receipt side-by-side.
- Test Drive original folder, four new file IDs/names/readback SHA and duplicate-retry check.
- How to rollback, operator audit link, any missing human privilege/owner budget decisions.
- Final state of 20221217/24/31 and next action. If no authorized V2 root deploy path, stop before change and report that precise blocker instead of using V1 command.

## Provider-free blackboard pause triage (optional, separately bounded)

If verified normalized FLAC and candidate SRT are available, run
`scripts/golden_v2_auto_gap_triage.py` **offline** from the exact reviewed
release; do not run heavy audio probes on the API thread. It creates
source/SRT-bound acoustic evidence using local FFmpeg/VAD with hard segment
limits. Verify `NONSPEECH_CONFIRMED` intervals are supported by contiguous
local probe measurements. Only positive-speech or uncertain spans remain
recommended for human review; none block the independent auto-tier.
No new cloud request, auto correction, or Golden PASS may result from this step.
