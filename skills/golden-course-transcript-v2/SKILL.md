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

- `POST /api/v1/golden-v2/jobs/{job_id}/approval` records a revision-bound job budget approval in the existing SQLite ledger.
- `POST /api/v1/golden-v2/jobs/{job_id}/repair-approval` records an exact assessment-SHA and window-bound repair-plan approval in SQLite.
- Both routes require the separate `COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED=true` flag and a strong `COURSE_TRANSCRIPT_V2_WRITE_TOKEN`; status routes use a different token. The configured per-job ceiling defaults to USD 10. These routes never submit a provider request, create a job, access Drive, or publish results. Keep both credentials server-side; do not put them in browser JavaScript.
- `scripts/golden_v2_local_worker.py` runs provider-free local ledger stages for one existing job and exits with an explicit blocker at provider/reviewer gates. It is not a persistent VPS worker or task-creation endpoint.

## Remaining task/handoff API (NOT IMPLEMENTED; placeholders, do not call as real endpoints)

```text
preview(source_url, model_mode) -> inventory, canonical candidates, estimated cost
submit_approved(preflight_id, source_sha, approval_revision) -> durable job_id
compare(job_id) -> ordered discrepancy windows, audio excerpt locators
publish(job_id, revision, owner_approved) -> Drive safe-publish + verified SHA
```

The read-only `job_status` snapshot exists in source as CLI/HTTP, and local `adjudicate` / `finalize` building blocks exist; they are not connected as these orchestration endpoints. Until a task operation is actually connected and tested, report `NOT_IMPLEMENTED` rather than inventing endpoint success.

## Gate and handoff discipline

Use a PR for each meaningful phase. Update `docs/GOLDEN_V2_GATE_STATUS.md` only with verified test/evidence links. Always report exact branch and commit, provider call counts/cost, Drive changed YES/NO, production changed YES/NO, reused raw evidence, Golden QA blockers, and next minimal action.

Do not let chat session length or a mobile connection determine job completion: durable VPS worker (once integrated) runs independently; ChatGPT/WebCodex resumes from its persisted status.
