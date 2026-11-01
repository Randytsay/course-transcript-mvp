# Golden V2 Auto Delivery — production rollout and operator acceptance

**Scope:** standalone `AUTO_ACCEPTED_WITH_REVIEW` SRT/TXT acceptance, user-friendly optional human review Markdown and structured QA JSON; **not** strict Golden PASS, ASR charge authorization, or proof of complete speech recognition. This release contains a **default-off** authenticated owner API and Drive/ledger publication bridge. This document is a release candidate runbook, **not** a record of production deployment.

## Contract (nonnegotiable)

1. Hash actual immutable `normalized.flac` and compare to canonical job/source SHA, same `source-preflight.json` identity, Drive original file ID/version/size/MD5.
2. Strict SRT cue syntax and nonoverlap, nonblank cues, within source audio duration; TXT must exactly mirror the ordered cue text. No AI hallucinated insertions. A technical failure produces `AUTO_BLOCKED_TECHNICAL`, no delivery.
3. `AUTO_ACCEPTED_WITH_REVIEW` means **technical artifact acceptance only**. Acoustic `REVIEW` gaps, incomplete Chirp/Gemini operations and unverified semantics are disclosed. No forced human listening. Silence and blackboard writing can be represented as unscripted gaps; a gap alone is not proof of omitted words.
4. Upload **four new files** to original Drive parent: `*_自動驗收_附複核建議.srt`, matching `.txt`, `*_自動驗收_驗收.json`, `*_自動驗收_人工建議複核.md`. Never rename, overwrite or delete original editorial files. Durable pre-generated IDs and all-file readback hashes protect against crash/duplicate uploads.
5. Owner-authenticated source- and revision-bound **publication** grant is separate from human listening/quality approval. `AUTO_DELIVERED` is written to `v2_auto_acceptance_deliveries` only after all four verified, under the shared Drive publication lock. Original `v2_jobs/v2_stages` remain entirely unchanged. No `GOLDEN_PASS` assertion.
6. Absolutely **no new paid speech/Gemini execution or retry** without existing source-bound signed cost authority. This API has no paid model path.

## Exact tested owner API

All paths are under `/api/v1/golden-v2/jobs/{job_id}/auto-delivery/`:

- `GET status`: authenticated read-only; independent `PREPARED` or `AUTO_DELIVERED` state.
- `POST prepare`: owner/write authentication + `COURSE_TRANSCRIPT_V2_AUTO_DELIVERY_API_ENABLED=true`; JSON `{ "revision": integer, "source_sha256": "64hex", "srt_filename": "selected.srt", "txt_filename": "selected.txt", "lesson_title": "...", "confirm": true }`. Requires the selected two **already staged** in the **same job's** private `golden-v2/` directory. Uses local `ffprobe`; hashes actual source; creates source/job-bound report files and `PREPARED` manifest. Does not touch Drive.
- `POST authorize-publication`: owner/write authentication, JSON `{ "manifest_sha256": "64hex", "revision": integer, "review_reference": "...", "confirm": true }`. Reads current Drive metadata and signs exact source/manifest/outputs. Does not attest listening.
- `POST publish`: owner/write authentication, JSON `{ "manifest_sha256": "64hex", "confirm": true }`. Validates signed grant and current source version, uploads four new files idempotently and verifies each readback SHA. Returns independent `AUTO_DELIVERED` receipt.

**Default** `COURSE_TRANSCRIPT_V2_AUTO_DELIVERY_API_ENABLED` is disabled. Read access also requires the existing status API credential; mutation requires existing owner write API token and a configured `COURSE_TRANSCRIPT_V2_GRANT_SECRET`. Never print token/secret values in logs.

## What is still not a complete hands-free ASR pipeline

The new API does **not** discover/choose/edit SRT/TXT by itself from Drive; a trusted intake/transcript stage must prepare two source-linked candidates in the job's private directory. It does not submit Chirp repairs or call Gemini, and it does not auto-sign per-job publication grants. Golden V2's previously designed acoustic inspector can classify `NONSPEECH_CONFIRMED`, `SPEECH_CONFIRMED`, and `REVIEW`, and its repair planner can produce bounded, source-hashed clip proposals, but paid submissions remain separately authorized. Semantic disputes remain report-only unless independently audio verified. Never describe these missing integrations as complete or live.

## Safe release progression — owner/operator only

1. Verify PR stack #159 → #161 → #162 → #163 → the new delivery bridge PR; exact full SHA CI/Release Compose success and independent code/security review. All are draft/unmerged as of initial preflight. A passing CI does not authorize privileged operations.
2. **Golden V2-only** deployment: last observed production is image `82e0cd45aa7ad80d776e2dcd0b0cfe5383eeede0`; API/intake running, paid/delivery stopped. Existing root-owned backup at `/opt/course-transcript-v2-chat/backups/pr159-20261029T071713Z` must not be remade/overwritten, and its validity must be verified by an authorized operator. Obtain a root-approved, exact-SHA **Golden V2-specific** deploy and rollback route; `sudo webcodex-deploy course-transcript <sha>` is **V1** and must NOT be used for this project.
3. Before any Production change: root-authorized consistent snapshot/integrity verification, exact image/source lineage, disk headroom, WAL and concurrent writer activity, connection to exact original Drive folders, independent emergency rollback drill; preserve original V1 service and ALL old receipts. Do not loosen ACLs/sudo allowlists, copy credentials or bypass guardrails.
4. Deploy only API + intake canary with **auto-delivery disabled initially**; verify `/health`, authenticated read-only status, then explicitly enable feature with owner approval. Paid and delivery workers stay STOPPED until a separate scoped decision.
5. For a single source-bound sandbox job first: stage exact candidate SRT/TXT; call `prepare`/`authorize-publication`/`publish`; confirm four remote Drive files, exact SHA, separate `AUTO_DELIVERED` receipt, original files unchanged, original Golden stages unchanged. Repeat exact request and verify no duplicate IDs; failure/recovery test fourth upload and source/version drift **without touching historical original files**.
6. Only after canary: evaluate 20221217, 20221224, 20221231 independently with actual source/ledger revision. Prepare existing human-edited reference candidates as **provisional**, preserving acoustic unknowns, then enable four-file auto delivery. Historical `PROCESSING`/Chirp PENDING must remain accurately reflected; don't replay/charge old LROs.
7. For 24/7 hands-free new-course workflow, separately build/validate a default-off watcher with authenticated Drive pairing, resume-safe Chirp operation reconciliation, bounded local-only acoustic work, spend-controlled repair, optional Gemini context analysis and restricted auto text insertion; commission only after end-to-end canary. Do not claim this phase is already implemented.

## Acceptance evidence

Provide release SHA, Docker image digest, CI/Compose links, checksum-verified backup & restore drill (no overwrite of active DB), feature flags **names and boolean values only**, single-course four-file original-folder Drive IDs/SHAs, source observation/revision, independent `AUTO_DELIVERED` ledger query, original `v2_jobs/v2_stages` rows compared before/after, canary retry evidence, worker heartbeat, rollback instructions. Any blocker must yield `NO-GO` and no false production claim.
