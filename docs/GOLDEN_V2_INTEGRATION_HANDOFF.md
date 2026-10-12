# Golden V2 integration handoff — non-paid durable-stage milestone

> Date: 2026-10-12 (Asia/Taipei)  
> Branch: `feat/golden-v2-gemini38-primary` (PR #143, **draft; NOT merged, NOT deployed**).  
> Work in this document is an isolated engineering scaffold, **not a runnable replacement for the production Chirp-first Worker**.

> **Goal continuation, 2026-10-12:** Additional provider-free code and tests are being delivered separately on `codex/golden-v2-goal-completion`; the original PR #143 still tracks `feat/golden-v2-gemini38-primary`. This entry describes local source implementation only. CI must be checked against the *new commit SHA* before it can be called green; live billing, Drive and production remain untouched.

### Chirp worker interruption between windows

`GoldenV2Ledger.resume_chirp_submissions()` can rotate a stale `chirp_independent` claim with a job-revision CAS after verifying every previously attempted window has a persisted operation ID. It then allows the worker to submit only remaining `PLANNED` windows. The stage heartbeat and recovery grace avoid taking over an active worker; rotating the claim invalidates the old worker. Any `SUBMITTING` row is an ambiguous provider POST and remains frozen for manual reconciliation. This closes the known-operation/unsent-window restart case; it does not reconcile an operation ID lost after a POST or retry failed provider operations.

After integrating the bounded saved-LRO recovery worker, local regression passed **815 full unittest**, **124 V2 unittest**, **27 handoff pytest**, Python compileall and `git diff --check`. Source commit `001ed9b9ecb1ef3d23df0b3e944bbc35cb7b065e` passed exact-SHA [GitHub CI #38198541708](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38198541708) and [Release Compose #38198541691](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38198541691). All tests used local/fake providers; no paid provider call, GCS/Drive write, or production change occurred.

## Newly integrated Goal-path components (isolated branch)

| Layer | Code | Current verified boundary |
|---|---|---|
| Crash-safe raw provenance | `app/providers/golden_v2_immutable.py`, `golden_v2_source_lineage.py` | Byte-exact, hardlink no-overwrite evidence for source manifest and raw provider JSON, tested with simulated conflicts and interrupted writes |
| Persisted recoverability | `app/jobs/golden_v2_ledger.py` | Revision CAS, local stale-claim recovery, strict ban on implicit retry of unknown paid requests |
| Persisted Speech LRO recovery worker | `app/pipeline/golden_v2_chirp_recovery_worker.py`, `tests/test_golden_v2_chirp_recovery_worker.py` | Bounded GET/recover pass from saved operation identities, restart/idempotence tests with SQLite and fake Speech/GCS, and a real Speech V2 client factory. Never retries POST, refuses unknown SUBMITTING and verifies completed evidence SHA; no live Speech request, invoice reconciliation or deployed scheduler |
| Acoustic coverage and Standard repair | `app/providers/chirp_completeness_gate.py`, `golden_v2_acoustic_qa.py`, `app/pipeline/golden_v2_chirp_repair.py`, `app/providers/golden_v2_audio_staging.py`, `app/providers/golden_v2_gcs_staging.py` | Positive VAD speech evidence is required for a repair proposal; exact assessment/source/clip/target/output identities and a cost ceiling are owner-approved in SQLite; clips are ≤5 minutes and GCS writes are immutable/generation guarded; restart-safe upload and Standard-only submission; target-only merge preserves base/raw evidence; compare remains blocked until acoustic PASS. Fake providers/local audio only; live speech/coverage UAT remains open |
| Independent acoustic stage | `app/pipeline/golden_v2_chirp_stage.py`, `app/providers/golden_v2_chirp_gateway.py`, `app/jobs/golden_v2_ledger.py` | Speech V2 adapter and fake-provider asynchronous submission/recovery; revision-bound restart may submit remaining PLANNED windows when prior operation IDs are known, while ambiguous SUBMITTING stays frozen; no actual Speech-to-Text call in this milestone |
| Optional third stage | `app/pipeline/golden_v2_third_stage.py` | Budget-checked optional provider with isolated evidence; offline tests only |
| Local adjudication flow | `app/pipeline/golden_v2_coordinator.py`, `golden_v2_stage_runner.py`, `app/providers/golden_v2_acoustic_qa.py` | SQLite-based comparison → block disputed speech → provider-free QA; no artificial human sign-off |
| Subtitle exports | `app/subtitles/golden_v2_render.py` | SRT/TXT from original Chirp word timestamps; output validity and text equivalence checked locally |
| Operator entry point | `scripts/golden_v2_local_worker.py` | One pass of safe local stages, explicit provider/reviewer blockers, no cloud calls or Drive writes |
| Verified overlap adjudication | `verify_primary_overlap_artifacts`, `finalize_reviewed_primary_overlaps`, `scripts/golden_v2_review_overlaps.py` | Fail-closed source/lineage/window/raw-audit SHA and model identity checks; manual per-seam listened-audio decisions produce separate immutable reviewed artifact and revision-CAS resume; local reviewer self-attestation cannot prove listening |
| Comparison candidate review bundle | `app/pipeline/golden_v2_review_bundle.py`, `scripts/golden_v2_review_candidates.py` | Read-only inspect validates source, normalized audio, provider evidence, comparison SHA, candidate snapshots and revision; apply requires explicit listening attestation, checks exact Chirp word corrections, claims only `adjudication`, and performs no provider or Drive calls. Reviewer identity/listening remain self-attested; not ChatGPT-connected |
| Owner task/status/approval API | `app/jobs/golden_v2_read.py`, `app/golden_v2_routes.py`, `app/jobs/golden_v2_ledger.py` | Source-SHA job registration creates `AWAITING_APPROVAL`; source-byte preflight remains the trusted caller's responsibility. Status read has its own token; job/repair approval has a separate write token and feature flag, configured actor, exact revision/confirmation, and default US$10 per-job ceiling. Mutations write SQLite only and never call a provider. Disabled by default; not deployed or exposed to a browser |

The previous acoustic milestone source SHA `de52cc9af27ac13d91210f0c65f4d5cde178a00b` passed **824 full unittest**, **33 focused repair/QA/worker unittest**, **27 handoff pytest**, Python compileall, and `git diff --check`. Exact-SHA [GitHub CI #38204588170](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38204588170) and [Release Compose #38204588132](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38204588132) both passed, including frontend tests/build, Docker image build, and Compose validation.

The next isolated source SHA `2c3243ad976b3f1c45ca16a0c4cedc5392e50436` separates Vertex Gemini 3.7 text correction from generic Vertex text generation, adds disabled-by-default owner job/repair approval endpoints that only mutate the SQLite ledger, and preserves the $10 default per-job approval ceiling. Local validation passed **831 Python unittest**, **27 handoff pytest**, **54 frontend tests**, Next.js production build, Python compileall, and `git diff --check`. Exact-SHA [GitHub CI #38206197000](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38206197000) and [Release Compose #38206197011](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38206197011) both passed, including Python 3.12, frontend tests/build, Docker image, Compose and release provenance checks. Google lists Gemini 3.7 Flash retirement on **2027-01-28**; the production plan must migrate before that date. All external calls remain fake/offline. No live Gemini, Chirp, reviewer API, long-audio benchmark, Drive publication, or production cutover has been validated. The complete G0–G8 verdict remains **0/9 PASS**.

The next source SHA `2d74acbbb89bfdc7f43cc8abffa4b7ad93d9f017` adds `golden_v2_review_candidates` inspect/apply bundles for offline, source-SHA/revision-bound manual candidate adjudication. Import revalidates immutable comparison candidates and exact Chirp word identities and uses the ledger's expected-stage guard to avoid claiming recognition work. Listening and reviewer identity remain operator self-attestations. Local verification passed **835 Python unittest**, **27 handoff pytest**, compileall, CLI help and `git diff --check`; exact-SHA [GitHub CI #38207135117](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38207135117) and [Release Compose #38207135128](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38207135128) both passed. The V2 Skill draft was corrected to identify the disabled/undeployed approval routes and review CLI as source-level features, while keeping job creation, persistent VPS execution and ChatGPT/WebCodex transport explicitly unimplemented. No live provider, Drive/GCS write, IAM/firewall change or production action occurred; G0–G7 remain PARTIAL, G8 NOT_RUN, 0/9 PASS.

Acoustic probing is limited to 16 gaps and 30 seconds per gap; longer/unexamined gaps remain REVIEW. All repair tests use fake Speech/GCS and local FLAC.

## Implemented components (actual code paths)

| Layer | Code | Contract |
|---|---|---|
| Primary recognition | `app/providers/gemini_primary_audio.py` | Gemini 3.8 audio-first Vertex request adapter, GCS window plan/identity, immutable raw response, truncated/rejected response review; never auto-repeat unknown paid call |
| Verified local audio | `app/providers/golden_v2_audio_staging.py` | Real FFmpeg normalized-FLAC clipping, source SHA, clip SHA, manifest, duration checks, tamper detection; no API calls |
| Storage staging | `app/providers/golden_v2_gcs_staging.py` | Live claim approval gate, content-addressed private-bucket GCS upload, generation precondition, metadata/size revalidation, no overwrite; tested only with fake GCS |
| Isolated primary stage integration | `app/pipeline/golden_v2_primary_stage.py` | Cost-preflight → local slice → approved GCS → Gemini 3.8 → immutable report + SQLite COMPLETE/REVIEW/UNKNOWN; real FFmpeg and fake cloud/model integration tests, **not enabled in production** |
| Text correction | `app/providers/correct_text.py`, `correct_text_cascade.py` + related API/Worker/Proxy/console defaults | Vertex Gemini 3.7 text-only correction; separate from the V2 Gemini 3.8 full-audio primary; raw records retain their actual model ID |
| Independent acoustic comparison | `app/providers/golden_v2_compare.py` | Gemini/Chirp normalized text disagreement, original Chirp evidence/timestamps only; **not** speech coverage or correctness proof |
| Reuse existing Chirp PASS | `app/pipeline/golden_v2_chirp_reuse.py` | Offline adoption requires the exact normalized FLAC SHA, PASS+handoff acoustic gate, original Chirp operation artifact and valid word chronology; historical provider cost not billed to the new V2 job; **new Chirp submit/recover still not integrated** |
| Ordered mode abstraction | `app/pipeline/golden_v2_flow.py` | Gemini → independent Chirp → optional third model, mocks only; long overlapping windows require reconciliation before use |
| Optional third recognition | `app/providers/gemini_third_transcribe.py` | Vertex `gemini-3.5-transcribe`, VERBATIM, conservative ≤15-minute windows, model cost approval, immutable raw evidence |
| Final QA gate | `app/providers/golden_v2_final_gate.py` | Structural SRT, SRT/TXT text equivalence, real Chirp word boundaries, reviewed audio evidence, strict fail closed |
| SQLite durability | `app/jobs/golden_v2_ledger.py` | Additive `v2_*` tables; owner approval revision, stage claim, source SHA, staged GCS window digest, cost reservation, restart BLOCKED/UNKNOWN, evidence-only reconciliation |
| Owner API | `app/golden_v2_routes.py` | Read and separately gated job/repair approval routes; independent bearer tokens, configured actor, default US$10 per-job ceiling, SQLite-only approval writes, no provider submission; not deployed |
| Local stage bridge | `app/pipeline/golden_v2_stage_runner.py` | Provider-free comparison and QA reports; stage outcomes persist in SQLite; NOT_YET paid Chirp or final Drive publisher |
| Model benchmark | `app/evaluation/golden_v2_benchmark.py` | Offline CER against *human-verified* gold, not model majority votes |
| Operator inspection | `scripts/golden_v2_status.py` | Read-only database task status; no token/approval secret, no DB migration, Drive changes or provider calls |
| Future conversation interface | `skills/golden-course-transcript-v2/SKILL.md` | Draft command/authorization spec only; not installed and not yet connected to an API |

## Non-paid verification

Run on a fresh isolated branch checkout with no credentials required:

```bash
python -m compileall -q app tests scripts
python -m unittest discover -s tests -p 'test_golden_v2_*.py' -v
python -m unittest discover -s tests -v
python -c 'from app.api_hardened import app; assert app.title'
python -c 'import app.pipeline.dynamic_worker_production'
npm --prefix frontend ci
npm --prefix frontend run build
docker compose --profile web config --quiet
```

Read persisted status (only for V2 test DBs/currently installed V2 jobs):
```bash
python scripts/golden_v2_status.py \
  --database /path/to/verified-v2-ledger.db \
  --job-id v2_<REAL_JOB_ID>
```

Prepare a local candidate review template, listen to the exact normalized audio, then edit `review_decisions` and any source-word-bound `replacements` before applying. The import requires the same source file and current ledger revision; it is only an operator attestation, not independent proof of listening:
```bash
python -m scripts.golden_v2_review_candidates inspect \
  --database /path/to/verified-v2-ledger.db \
  --job-id v2_<REAL_JOB_ID> \
  --job-dir /path/to/verified-job-dir \
  --output /path/to/local-review.json
python -m scripts.golden_v2_review_candidates apply \
  --database /path/to/verified-v2-ledger.db \
  --job-id v2_<REAL_JOB_ID> \
  --job-dir /path/to/verified-job-dir \
  --normalized-audio /path/to/normalized.flac \
  --bundle /path/to/local-review.json \
  --reviewer local-operator \
  --review-reference listening-session-id \
  --confirm-listened
```

Test evidence is supplied by GitHub Actions for the exact commit. **Do not copy PASS from an older commit to this milestone without checking the head commit's CI.** In particular, frequent branch commits can CANCEL older in-progress CI runs, which is not the same as a test failure.

## Engineering caveats / true blockers

1. **P0:** WebCodex's previously registered source/worktree projects show Git unavailable/broken; no reset/clean/stash was attempted. Verify real VPS production SHA, active paid jobs, leases, actual source checkout and safe DB/data backup before any deployment. Work on GitHub isolated branch only until a healthy Runner worktree can be created. Do not invent verified production status.
2. **P1:** Real Vertex Gemini 3.8 audio and Gemini 3.7 text-correction availability/config/roles/cost each still require owner-approved smoke; changed defaults are only in the Draft PR. Google currently lists Gemini 3.7 Flash retirement on 2027-01-28, so approve a tested replacement before production or that date. Historical evidence is not rewritten. [Google model lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-7-flash)
3. **P2:** Local normalized-FLAC chunk extraction, SHA revalidation, GCS upload adapter and Gemini primary stage are implemented and tested with offline fake clients. **Local original-file SHA → normalized FLAC SHA → clip SHA manifests and byte-bound raw audit verification are implemented.** Independent original Drive object attestation, on-VPS bucket permissions, real Vertex pricing and end-to-end Worker wiring remain unverified. Long overlapping windows fail closed pending manual seam audio review; whole-file acoustic coverage remains unverified.
4. **P3:** Existing PASS-approved Chirp evidence can now be imported after Gemini primary when source SHA and acoustic evidence agree. Fake-provider V2 submission/recovery and offline Standard bounded repair are connected in one durable Chirp stage: positive speech evidence, exact owner-approved ranges/cost, Standard-only submit, crash-safe re-upload, target-only word replacement, and acoustic PASS before comparison. Production Worker/scheduler, invoice-bound billing, real Speech/VAD long-audio validation and human listening remain outstanding. Raw evidence and provider timing remain authoritative; final V2 Golden QA is mandatory.
5. **P4:** Real Vertex 3.5 Transcribe SDK/endpoint smoke and provider usage pricing are not complete. Per current Vertex documentation, diarization+vocabulary is illustrated as supported; word_timestamp+vocabulary is conservatively kept separate. Taiwanese/Hokkien specific performance is **not yet verified**; test with real gold recordings. Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-transcribe
6. **P5:** A local source-bound candidate review/export-import path now exists, but ChatGPT/WebCodex session transport and independently authenticated reviewer evidence remain unimplemented. No automatic difference resolution exists.
7. **P6:** No trusted benchmark dataset or actual bill yet; current CER is only a calculation library.
8. **P7:** SQLite stage claim and provider adapter authorization are tested with mock clients; the read-only owner HTTP API/CLI and separately gated authenticated job/repair approval routes are implemented, but no endpoint or continuously running V2 Worker has been deployed and no owner UI/Skill is connected. Approvals only update the ledger; they never submit providers. Crash/outcome-unknown stays blocked until independent evidence verifier approves reconciliation.
9. **P8:** UI and official Drive delivery hook are not integrated; final QA verdict does **not** perform publication. Existing V1 safe-publish remains intact.

## Recommended immediate implementation sequence

1. Finish the **current exact commit** CI, fix failed tests if any. Keep both PR #143 (base) and PR #144 (V2 Goal continuation) in draft; do not merge to main or deploy.
2. Complete **original Drive source → normalized FLAC SHA provenance attestation** and on-VPS validation of private GCS bucket, cost estimator and local clip staging already implemented behind approval; prove safe cloud retries with exact object identity.
3. Connect the isolated Gemini stage executor to a **resumable V2 Worker** and existing V1 hardened asynchronous Chirp Dynamic/Standard submit/recovery. Prefer the new read-only Chirp PASS reuse path when source-matched. Add per-window cost accounting and asynchronous provider-operation reconciliation so partial multi-window charges are never hidden. No paid ASR until exact source, model and per-window cost are approved.
4. Validate the newly added local manual overlap-seam reconciliation on actual consented recordings; add independently authenticated reviewer evidence and complete source VAD/Coverage checks. Current CLI records operator attestation only and cannot prove audio was heard.
5. Route vetted comparison evidence into existing ChatGPT handoff, repair only verified omissions, and pass all Golden QA/Drive revision lock checks.
6. After owner approves each live smoke and the aggregate ≤US$10 test budget, do short cases, then benchmark real Mandarin/Taiwanese/scripture, followed by long audio regression.
7. The owner API now includes separately gated bearer-authenticated job and exact-plan repair approvals (SQLite-only; it never submits provider calls). Add the owner UI/Skill handoff and validate the disabled-by-default routes in staging before proposing production migration.

## Mandatory next-agent report

Exact repo/ref/SHA and CI run IDs; dirty/live worktree state; provider calls and dollars spent; Drive mutated YES/NO; production mutated YES/NO; resumed provider evidence vs new attempts; QA PASS/BLOCKED and unresolved exact-gate IDs. For scope and owner approvals see `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`.
