# Golden Course Transcript V2 — Draft ChatGPT/WebCodex Operator Skill

> **Status: DRAFT ONLY (P7 NOT YET INTEGRATED).** This repository file is NOT an installed/operational ChatGPT Skill. Do not claim a V2 worker is available merely because the file exists. Existing `skills/golden-course-transcript/SKILL.md` remains the production-compatible contract until formal migration.

## Purpose

Accept natural-language requests for transcription, model comparisons, rechecks and QA of Google Drive media, delegating durable work to the project's owner-authorized Course Transcript worker. Use this proposed V2 sequence:

```text
inventory / lineage / non-paid preflight / budget approval
→ Gemini 3.8 Flash audio-first complete main transcript
→ independent Chirp 3 word-level timeline and audio coverage
→ optional Vertex Gemini 3.5 Transcribe (QUALITY_3WAY or SMART_2PLUS)
→ ChatGPT/WebCodex evidence-guided discrepancy adjudication
→ deterministic SRT/TXT rendering from Chirp evidence
→ Golden QA / safe-publish / readback hash verification
```

Reference specifications:
- `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md`
- `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`
- `docs/GOLDEN_V2_GATE_STATUS.md`
- `CURRENT_STATE.md`, `AGENTS.md`, `DECISIONS.md`

## Commands to interpret

- `黃金字幕：<Drive URL>，用三引擎，完成後回寫原資料夾`
- `這批用 Gemini 3.8 + Chirp，第三引擎先不跑`

Model boundary: Gemini 3.8 Flash is the full-audio primary recognizer. Use Vertex Gemini 3.7 Flash only for text-only correction of existing ASR text; never send audio to the correction adapter. Preserve each provider's raw result and actual model ID. Do not use Gemini 2.5.
- `比較三套模型的逐字稿、經文、台語、處理費用`
- `續跑 job <id>，只修還沒過的差異`
- `列出待人工查核片段及附近音訊`
- `驗收完成了嗎？提供 QA evidence、當前 Git SHA 與 Drive 證據`

## Hard requirements

1. Always discover actual task state from durable job ledger; do not infer progress from chat history, prior replies or a merely deployed UI.
2. Prior to any paid model submission: identify canonical media/source SHA, stage/attempt ID, matching approval for exact source and estimated cost, and remaining batch/project budget. V2 initial testing has a proposed separate USD 10 aggregate limit, not blanket authorization.
3. Do not start fresh Chirp when the Gemini primary transcript has not passed its structural source-window integrity gate. Chirp first recognition must not receive Gemini transcript as an ASR reference.
4. For Vertex 3.5 Transcribe, use conservative 15-minute bounded audio windows, VERBATIM for faithful comparison. If disabled, no API call.
5. Never infer accuracy from the number of agreeing models. Compare to independently human-reviewed gold (CER/WER) and check source-audio/VAD evidence.
6. Every corrected reading cue time must originate from validated Chirp word boundaries, never ChatGPT/Gemini timestamp guesses. Missing acoustic anchors = REVIEW.
7. Read scripture, presentation and same-course references only for spelling/context; actual speech outranks canonical book text. Taiwanese/Hokkien uncertain terms = linked audio review.
8. V2 speech transcript + model correction results must preserve raw provider requests/responses/usage/error records; unknown provider completion must not auto-resubmit.
9. Stop at unresolved Golden blockers; no auto-publish, no knowledge ingestion. `completed` does not imply `GOLDEN_PASS`.
10. Drive safe-publish must preserve original media and manually authored transcript, use revision lock, pending+verify+backup+promote+readback hash. A Drive failure never repeats paid ASR.
11. Do not overwrite, reset, clean or stash a dirty active development/production worktree; deployment is from a reviewed exact SHA only.
12. User approval required for IAM/billing/security changes, per-source first real paid smoke, and production cutover/Drive publish if no pre-existing scoped authorization.

## Implemented source-level read-only operator status (NOT DEPLOYED)

- `python scripts/golden_v2_status.py --database /path/to/ledger.db --job-id v2_<actual-id>` reads only existing SQLite data.
- Disabled-by-default `GET /api/v1/golden-v2/jobs` and `GET /api/v1/golden-v2/jobs/{job_id}` require `COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED=true`, a separate strong `COURSE_TRANSCRIPT_V2_STATUS_TOKEN` Bearer credential and an existing `COURSE_TRANSCRIPT_V2_LEDGER_PATH`. This is a trusted backend-only status route; it is not a user-browser token or proof of production availability.
- `python scripts/golden_v2_review_overlaps.py --database ... --job-id ... --job-dir ... --normalized-audio ...` provides read-only pending-seam inspection. Applying JSON decisions also requires `--decisions-json`, `--reviewer`, `--review-reference`, `--confirm-listened`; this is local operator **self-attestation**, not independent human listening proof. Every overlap is tied to verified immutable raw response SHA, source lineage, audio SHA, original stage report and revision-CAS. No whole-file Golden PASS results from a seam review. Do not expose this CLI through HTTP.
- `python -m scripts.golden_v2_review_candidates inspect ...` writes a new source/revision-bound candidate review template; `apply ... --reviewer ... --review-reference ... --confirm-listened` imports decisions only after rechecking audio SHA, provider evidence, comparison SHA, candidate snapshot, and ledger revision. `CORRECT_CHIRP` edits must bind to exact Chirp word evidence. This is still operator **self-attestation**, does not prove independent listening, and performs no provider or Drive calls.

## Implemented source-level owner approval API (NOT DEPLOYED)

- `POST /api/v1/golden-v2/jobs` registers a source SHA, mode, and per-stage integer-cent estimates in the existing SQLite ledger. It creates an `AWAITING_APPROVAL` job only; the trusted caller must have independently established that the SHA identifies the intended normalized audio. This endpoint does not fetch or attest source media.
- `POST /api/v1/golden-v2/jobs/{job_id}/approval` records a revision-bound job budget approval in the existing SQLite ledger.
- `POST /api/v1/golden-v2/jobs/{job_id}/repair-approval` records an exact assessment-SHA and window-bound repair-plan approval in SQLite.
- All three write routes require the separate `COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED=true` flag and a strong `COURSE_TRANSCRIPT_V2_WRITE_TOKEN`; status routes use a different token. The configured per-job ceiling defaults to USD 10. They never submit a provider request, access Drive, or publish results. Keep both credentials server-side; do not put them in browser JavaScript.
- `scripts/golden_v2_local_worker.py` runs provider-free local ledger stages for one existing job and exits with an explicit blocker at provider/reviewer gates. It is not a persistent VPS worker or task-creation endpoint.

## Implemented local source preflight (NOT a Drive importer)

- `scripts/golden_v2_source_preflight.py` accepts an already-downloaded local original plus a local JSON observation captured by a separate read-only Drive importer. Required observation fields are `provider: "google_drive"`, `file_id`, `version`, `size_bytes`, and `downloaded_sha256`.
- The command snapshots and hashes the local bytes before FFmpeg, creates a 16 kHz mono FLAC, preserves both original and normalized files, and writes immutable source-lineage and preflight manifests. The bytes must match the supplied observation. This verifies consistency only; it does not authenticate Drive metadata, fetch a Drive URL, or upload anything.
- It creates an idempotent `AWAITING_APPROVAL` SQLite job bound to the manifest digest. `--estimate STAGE=CENTS` must be supplied once for every ledger stage; the local estimate ceiling defaults to 1000 cents (USD 10). Estimates are caller input, not provider quotes and not approval. The command never starts providers, advances a stage, or changes Drive/GCS.

Example (replace the observation path and local source with the result of an authorized read-only download; include all six stage estimates):

```bash
python scripts/golden_v2_source_preflight.py \
  --source /path/to/already-downloaded-original.wav \
  --drive-observation /path/to/local-drive-observation.json \
  --artifact-root /path/to/private-data/golden-v2 \
  --database /path/to/private-data/golden-v2/ledger.sqlite \
  --mode QUALITY_3WAY \
  --estimate gemini_primary=0 \
  --estimate chirp_independent=0 \
  --estimate compare=0 \
  --estimate third_optional=0 \
  --estimate adjudication=0 \
  --estimate golden_qa=0
```

The default-unenabled `golden-v2` Compose profile runs `scripts/golden_v2_worker_daemon.py` as a persistent, provider-free local-stage worker. Its container has no network and no cloud credentials. It processes only `QUEUED`/`PROCESSING` jobs with local artifacts and stops at provider, budget or human-review gates. The profile has not been enabled on VPS/production; this source-level worker is not proof of live task continuity.

## Local owner status console (IMPLEMENTED IN SOURCE; DISABLED / NOT DEPLOYED)

`/golden-v2` is a responsive status/approval console for jobs that already exist in the V2 SQLite ledger. It displays source SHA, mode, job/revision state, per-stage state and cost evidence. It can submit only a revision-bound budget approval for an `AWAITING_APPROVAL` job; approval updates SQLite and queues eligible local stages, but this UI does not create a source job, acquire Drive media, submit paid provider work, provide audio review, or publish files.

The Next.js server routes proxy to the owner API with separate status and write bearer tokens held only in server environment variables. Keep `COURSE_TRANSCRIPT_V2_ADMIN_UI_ENABLED` unset/false by default. When intentionally configured for a private staging environment, the frontend and API tokens must match their corresponding backend tokens; status/write API feature flags remain independently disabled until that environment is reviewed. The optional frontend Compose env file defaults to `/dev/null`; it is not created by this source change. Production access is restricted to the transcript admin hostname and depends on Cloudflare Access being enforced at the edge; approval POSTs additionally require same-origin HTTPS. Do not expose the owner console through the public reviewer hostname.

The UI currently cannot report real task status unless a ledger-backed owner API is configured and reachable. An unavailable ledger is shown as an unknown count, never as zero jobs. No live ledger, production token, Cloudflare policy, or deployment is implied by local browser review.

## Remaining task/handoff API (NOT IMPLEMENTED; placeholders, do not call as real endpoints)

```text
preview(source_url, model_mode) -> inventory, canonical candidates, estimated cost
compare(job_id) -> ordered discrepancy windows, audio excerpt locators
publish(job_id, revision, owner_approved) -> Drive safe-publish + verified SHA
```

Use `scripts/golden_v2_source_preflight.py` for an already-downloaded local source when its read-only Drive observation is available. It records an unapproved ledger job but does not acquire the Drive source. `POST /api/v1/golden-v2/jobs` remains a separate caller-attested registration option; its approval endpoint changes an approved job to `QUEUED`. The default-offline Compose worker then advances only available local stages. Drive URL acquisition, UI-based task creation, paid provider execution, reviewer handoff and publication are not connected as a complete operation; report those gaps as `NOT_IMPLEMENTED`.

## Gate and handoff discipline

Use a PR for each meaningful phase. Update `docs/GOLDEN_V2_GATE_STATUS.md` only with verified test/evidence links. Always report exact branch and commit, provider call counts/cost, Drive changed YES/NO, production changed YES/NO, reused raw evidence, Golden QA blockers, and next minimal action.

Do not let chat session length or a mobile connection determine job completion: durable VPS worker (once integrated) runs independently; ChatGPT/WebCodex resumes from its persisted status.
