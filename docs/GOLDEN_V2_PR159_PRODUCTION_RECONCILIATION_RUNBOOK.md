# Golden Transcript V2 PR #159 — controlled restoration runbook

**Status:** DRAFT / NO-GO. The code and its CI checks do not authorize Production
deployment, paid provider calls, changed job states, or Google Drive publication.

## Verified starting conditions (2026-10-29, Asia/Taipei)

- Production release: `82e0cd45aa7ad80d776e2dcd0b0cfe5383eeede0`.
- Existing owner-approved jobs (source/quote/revision/cost must be re-checked live):
  - 20221217 清淨心: `v2p_cce7a9147a495027bd16f5619ad17b8d`,
    rev 4, Chirp RUNNING; 34 reported completed external GCP operations,
    reported 136¢ Chirp cost, 9 acoustic review gaps. Official API records
    Gemini SKIPPED / 8¢ actual. **All 34 operation identities and live GCP
    results require independent verification.**
  - 20221224 齋醮: `v2p_437c811bbf615f42bf54f49ad439465d`,
    rev 3, Chirp PENDING; Gemini SKIPPED / 40¢ recorded in the official API.
  - 20221231 正法傳承: `v2p_48d9edc699da5d8ebe020d4f0b6afa8f`,
    rev 3, Chirp PENDING; Gemini SKIPPED / 16¢ recorded in the official API.
- Paid API is disabled, Paid Worker is stopped, Drive publication is disabled.
- Per-submitted-GDrive-folder standing auto-budget cap 600¢ is configured,
  **not enabled**. Do not treat signed chat requests as a server-side policy.
- WebCodex uid 999 cannot read host `/opt/course-transcript-v2-chat/data`
  (root-owned mode 0700). The root-owned
  `/usr/local/sbin/golden-v2-diagnostic` wrapper has an explicit list of
  only three older job IDs, none of the 202212xx jobs above.
  **Do not chmod, sudo-bypass, relax wrapper allowlists, access credentials,
  or perform ad-hoc docker exec without a scoped independent authorization.**

## G3: exact-SHA authenticated, read-only preflight

A reviewed release with PR159 code will include the owner-authenticated endpoint
`GET /api/v1/golden-v2/jobs/{job_id}/chirp-reconciliation-preview`.
It is not present in the original Production release; 404 there means
**undeployed** and must not be worked around.

The endpoint requires the existing private authorization header, reads only the
pre-existing isolated V2 SQLite Ledger (SQLite URI `mode=ro` +
`PRAGMA query_only=ON`) and local source/provider/acoustic files, and returns
non-secret per-window SHA/count/cost metadata.

The body must contain:

- `read_only=true`, `ledger_modified=false`,
  `provider_calls_started=false`, `drive_mutations_started=false`,
  `golden_qa_pass=false`,
  `live_gcp_provider_status_verified=false`.
- `normalized_flac_sha_verified=true`, `operation_count=34`,
  `all_operations_saved_done=true`, `local_evidence_reconciled=true`,
  and `blockers=[]` before further reconciliation is considered.
- For each saved window, `status=DONE`, `receipt_sha_verified=true`,
  and independently checked `actual_cents`. No missing receipts or unknown
  operation identities.
- Source-bound acoustic report(s) with `acoustic_gate=REVIEW` and the
  accurate count of **nine** review gaps (numbers must match actual report
  schema; do not rely on a candidate count alone).
- Known total actual cents = `chirp_window_known_actual_cents` + other-stage
  recorded actual cents; do not silently zero out SKIPPED-stage charges.

**IMPORTANT:** A local preview is NOT GCP confirmation; it does not establish
that provider LROs are truly terminal, that pricing/final invoices agree, or
that source uploads/Drive metadata are still current. It never authorizes
replays, QA PASS, payment, deployment or delivery.

An authorized operator must independently check the immutable GCP Operation
names against live `DONE/SUCCEEDED` responses, raw output hashes,
billed-duration evidence, cloud billing and any unknown/outcome-ambiguous rows.
Write the resulting GCP reconciliation report and SHA into immutable audit
evidence **without modifying Production**. All 34 must match exactly.

## Controlled file-level backup and restore rehearsal (not yet run on Production)

PR159 includes `scripts/golden_v2_backup_rehearsal.py`, which is **root-only**
at its command-line entrypoint. Its trusted, hardcoded source is the Golden V2
host data root and its separate target is the protected host backup directory.
It does not request or read credentials and does not access Google Cloud/Drive.

Under a separately approved, root-managed operator session, first stage and
verify the **exact reviewed Git SHA** of this script outside the active live
release. Confirm the correct host mount source, applicable snapshot retention,
real disk capacity, and quiesce any intake/paid/delivery writer before starting.
Do not copy an unreviewed chat code snippet directly into a privileged shell.

Illustrative commands **for an approved operator only**, from the verified
source checkout (not instructions for ordinary WebCodex uid 999):

```sh
python3 -m scripts.golden_v2_backup_rehearsal backup --label pr159-20261029T103500Z
python3 -m scripts.golden_v2_backup_rehearsal verify --label pr159-20261029T103500Z
```

Use a unique real UTC label; the example is not a pre-existing backup. The
script never overwrites a prior name. It creates an isolated private snapshot
containing `ledger.sqlite`, `jobs.tar.gz`, and a SHA manifest. SQLite's
online Backup API produces a consistent database snapshot even with WAL. It
detects source-ledger changes during archive creation, hashes each saved job
file, restores both SQLite and job files to a separate temporary directory,
checks database integrity/foreign keys and verifies each extracted file SHA.
It rejects symlinks, unsafe tar entries, insufficient disk space, duplicate
labels, and changed evidence. No provider request or Drive mutation is made.

**Boundary:** This is an isolated *file-level* restore drill; it is not proof
of complete host service recovery, rollback of Docker images, cloud resource
state, or the final invoice. The backup covers `ledger.sqlite` and `jobs/`
only, not host credentials, external storage, running containers, or other
data directories. A second authenticated service startup test against a
separate restored runtime (with network and paid paths disabled) is required
before any Production deployment. Never restore this backup over a live WAL
database.

## G4: release and recover (requires separate Owner sign-off)

1. Confirm target source commit, CI SHA, all SQL schema and all container image
   tags/worker entrypoints match. Do not restart a stopped worker with an older
   image. Record immutable image digest and rollout/rollback references.
2. Confirm disk/free space and journal mode. Quiesce all possible SQLite
   writers before consistent backup; use the SQLite online Backup API
   (or `VACUUM INTO` with isolation) and verify `PRAGMA integrity_check`.
   Preserve independent artifact copy and historical file/manifest SHA.
   Never replace a WAL-mode `.sqlite` file without coordinated WAL handling.
3. Rehearse restoration on an isolated copy and validate ledger, provider
   operation identities, artifact SHA and startup health. If no verified
   restoration path exists, **NO-GO**.
4. Perform exact-SHA, explicitly Owner-authorized deploy; inspect each
   container digest and API heartbeat. Keep paid and publish switches disabled.
5. On 20221217, rerun the read-only preview and independent GCP reconciliation.
   Under an **additional explicit scoped Owner grant**, reconcile **only**
   already-completed Chirp results, with no model POST, no Drive mutation and
   no Golden PASS. Log pre/post Ledger snapshots and new immutable evidence SHA.
6. Verify Chirp has exited paid RUNNING/global lock while acoustic
   `REVIEW_REQUIRED` is preserved. Only after separate raw-audio review,
   source/revision CAS and authorized bounded Standard repair proposals may
   any new paid repair be considered. Each cost remains within the original
   approval and applicable per-folder 600¢ total.

## G5–G10: true lesson delivery

- With valid source/quote/grant/price/live-Drive checks, dispatch new Chirp
  only for jobs whose provider operations truly have never been submitted;
  preserve receipts, disable SDK retries, stop on ambiguous outcomes.
- Resolve each acoustic QA interval with real audio evidence, human or verified
  adjudication; never label candidate `REVIEW` as `Golden PASS`.
- Generate versioned SRT and TXT; publish only under a separate Drive
  authorization and safe existing-file revision policy. Re-read Drive
  file ID + SHA; only then mark the corresponding Ledger row `DELIVERED`.
- Run a newly submitted folder end-to-end canary under a *persisted enabled*
  per-folder US$6 signed auto-policy (the current policy is disabled), verify
  dedupe, failure-recovery, and cross-file cumulative reservation.

## Decision gate

**Current decision: NO-GO for Production write/deploy and provider calls.**
A DRAFT PR, CI success, or a user's general request to keep working does not
substitute for the scoped Production controls above. Update Issue #160 with
live evidence and the exact next unblocker.
