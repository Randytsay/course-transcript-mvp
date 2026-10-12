# Golden V2 integration handoff — non-paid durable-stage milestone

> Date: 2026-10-12 (Asia/Taipei)  
> Branch: `feat/golden-v2-gemini38-primary` (PR #143, **draft; NOT merged, NOT deployed**).  
> Work in this document is an isolated engineering scaffold, **not a runnable replacement for the production Chirp-first Worker**.

> **Goal continuation, 2026-10-12:** Additional provider-free code and tests are being delivered separately on `codex/golden-v2-goal-completion`; the original PR #143 still tracks `feat/golden-v2-gemini38-primary`. This entry describes local source implementation only. CI must be checked against the *new commit SHA* before it can be called green; live billing, Drive and production remain untouched.

## Newly integrated Goal-path components (isolated branch)

| Layer | Code | Current verified boundary |
|---|---|---|
| Crash-safe raw provenance | `app/providers/golden_v2_immutable.py`, `golden_v2_source_lineage.py` | Byte-exact, hardlink no-overwrite evidence for source manifest and raw provider JSON, tested with simulated conflicts and interrupted writes |
| Persisted recoverability | `app/jobs/golden_v2_ledger.py` | Revision CAS, local stale-claim recovery, strict ban on implicit retry of unknown paid requests |
| Independent acoustic stage | `app/pipeline/golden_v2_chirp_stage.py`, `app/providers/golden_v2_chirp_gateway.py` | Fake-provider asynchronous Chirp operation submission/recovery and persisted result identity; no actual Speech-to-Text call in this milestone |
| Persisted Speech LRO recovery worker | `app/pipeline/golden_v2_chirp_recovery_worker.py`, `tests/test_golden_v2_chirp_recovery_worker.py` | Bounded GET/recover pass from saved operation identities, restart/idempotence tests with SQLite and fake Speech/GCS, and a real Speech V2 client factory. Never retries POST, refuses unknown SUBMITTING and verifies completed evidence SHA; **no live Speech request, independent invoice reconciliation, Standard repair or deployed scheduler** |
| Optional third stage | `app/pipeline/golden_v2_third_stage.py` | Budget-checked optional provider with isolated evidence; offline tests only |
| Local adjudication flow | `app/pipeline/golden_v2_coordinator.py`, `golden_v2_stage_runner.py`, `app/providers/golden_v2_acoustic_qa.py` | SQLite-based comparison → block disputed speech → provider-free QA; no artificial human sign-off |
| Subtitle exports | `app/subtitles/golden_v2_render.py` | SRT/TXT from original Chirp word timestamps; output validity and text equivalence checked locally |
| Operator entry point | `scripts/golden_v2_local_worker.py` | One pass of safe local stages, explicit provider/reviewer blockers, no cloud calls or Drive writes |
| Verified overlap adjudication | `verify_primary_overlap_artifacts`, `finalize_reviewed_primary_overlaps`, `scripts/golden_v2_review_overlaps.py` | Fail-closed source/lineage/window/raw-audit SHA and model identity checks; manual per-seam listened-audio decisions produce separate immutable reviewed artifact and revision-CAS resume; local reviewer self-attestation cannot prove listening |
| Owner status interface | `app/jobs/golden_v2_read.py`, `app/golden_v2_routes.py` | Read-only SQLite snapshot; disabled by default; separate strong server-to-server bearer token; not deployed or exposed to a browser |

The latest 2026-10-12 isolated worktree checks passed **807 full unittest**, **27 focused unittest**, **27 handoff pytest**, **54 frontend tests**, Python compilation, Next.js build (19 pages), Compose config, and whitespace verification. These are source-level checks; no live Gemini 3.8, Gemini 3.5 Transcribe, Chirp 3, reviewer API, long-audio benchmark, Drive publication, or production cutover has been validated. The complete G0–G8 verdict is **0/9 PASS** until the defined evidence is collected.

## Implemented components (actual code paths)

| Layer | Code | Contract |
|---|---|---|
| Primary recognition | `app/providers/gemini_primary_audio.py` | Gemini 3.8 audio-first Vertex request adapter, GCS window plan/identity, immutable raw response, truncated/rejected response review; never auto-repeat unknown paid call |
| Verified local audio | `app/providers/golden_v2_audio_staging.py` | Real FFmpeg normalized-FLAC clipping, source SHA, clip SHA, manifest, duration checks, tamper detection; no API calls |
| Storage staging | `app/providers/golden_v2_gcs_staging.py` | Live claim approval gate, content-addressed private-bucket GCS upload, generation precondition, metadata/size revalidation, no overwrite; tested only with fake GCS |
| Isolated primary stage integration | `app/pipeline/golden_v2_primary_stage.py` | Cost-preflight → local slice → approved GCS → Gemini 3.8 → immutable report + SQLite COMPLETE/REVIEW/UNKNOWN; real FFmpeg and fake cloud/model integration tests, **not enabled in production** |
| Existing correction target | `app/providers/correct_text.py`, `correct_text_cascade.py` + related API/Worker/Proxy/console defaults | V2 branch replaces 3.7 target with 3.8; historical V1 raw records must stay unchanged |
| Independent acoustic comparison | `app/providers/golden_v2_compare.py` | Gemini/Chirp normalized text disagreement, original Chirp evidence/timestamps only; **not** speech coverage or correctness proof |
| Reuse existing Chirp PASS | `app/pipeline/golden_v2_chirp_reuse.py` | Offline adoption requires the exact normalized FLAC SHA, PASS+handoff acoustic gate, original Chirp operation artifact and valid word chronology; historical provider cost not billed to the new V2 job; **new Chirp submit/recover still not integrated** |
| Ordered mode abstraction | `app/pipeline/golden_v2_flow.py` | Gemini → independent Chirp → optional third model, mocks only; long overlapping windows require reconciliation before use |
| Optional third recognition | `app/providers/gemini_third_transcribe.py` | Vertex `gemini-3.5-transcribe`, VERBATIM, conservative ≤15-minute windows, model cost approval, immutable raw evidence |
| Final QA gate | `app/providers/golden_v2_final_gate.py` | Structural SRT, SRT/TXT text equivalence, real Chirp word boundaries, reviewed audio evidence, strict fail closed |
| SQLite durability | `app/jobs/golden_v2_ledger.py` | Additive `v2_*` tables; owner approval revision, stage claim, source SHA, staged GCS window digest, cost reservation, restart BLOCKED/UNKNOWN, evidence-only reconciliation |
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

Test evidence is supplied by GitHub Actions for the exact commit. **Do not copy PASS from an older commit to this milestone without checking the head commit's CI.** In particular, frequent branch commits can CANCEL older in-progress CI runs, which is not the same as a test failure.

## Engineering caveats / true blockers

1. **P0:** WebCodex's previously registered source/worktree projects show Git unavailable/broken; no reset/clean/stash was attempted. Verify real VPS production SHA, active paid jobs, leases, actual source checkout and safe DB/data backup before any deployment. Work on GitHub isolated branch only until a healthy Runner worktree can be created. Do not invent verified production status.
2. **P1:** Real Vertex Gemini 3.8 provider availability/config/roles/cost still requires owner-approved smoke; changed model defaults are only in PR. The 3.7 historical evidence is not rewritten.
3. **P2:** Local normalized-FLAC chunk extraction, SHA revalidation, GCS upload adapter and Gemini primary stage are implemented and tested with offline fake clients. **Local original-file SHA → normalized FLAC SHA → clip SHA manifests and byte-bound raw audit verification are implemented.** Independent original Drive object attestation, on-VPS bucket permissions, real Vertex pricing and end-to-end Worker wiring remain unverified. Long overlapping windows fail closed pending manual seam audio review; whole-file acoustic coverage remains unverified.
4. **P3:** Existing PASS-approved Chirp evidence can now be imported into V2 *after* Gemini primary when the normalized FLAC SHA, original operation/plan and acoustic completeness PASS all agree. Fake-provider V2 asynchronous Chirp submission/recovery has a durable local stage; a bounded, restart-safe recovery pass now supports the production Speech V2 client factory but has not run against a live provider. Approved paid submission orchestration, verified billing inputs and Standard bounded repair Worker remain outstanding. Imported evidence retains provider timing; final V2 Golden QA remains mandatory.
5. **P4:** Real Vertex 3.5 Transcribe SDK/endpoint smoke and provider usage pricing are not complete. Per current Vertex documentation, diarization+vocabulary is illustrated as supported; word_timestamp+vocabulary is conservatively kept separate. Taiwanese/Hokkien specific performance is **not yet verified**; test with real gold recordings. Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-transcribe
6. **P5:** Actual ChatGPT/WebCodex audio evidence adjudication and handoff imports remain to implement; no automatic difference resolution exists.
7. **P6:** No trusted benchmark dataset or actual bill yet; current CER is only a calculation library.
8. **P7:** SQLite stage claim and provider adapter authorization are tested with mock clients; the read-only owner HTTP API and CLI are implemented, but no endpoint or continuously running V2 Worker has been deployed, and no authenticated mutation API exists. Crash/outcome-unknown stays blocked until independent evidence verifier approves reconciliation. Status can be read with the CLI or disabled-by-default token-gated HTTP routes in source.
9. **P8:** UI and official Drive delivery hook are not integrated; final QA verdict does **not** perform publication. Existing V1 safe-publish remains intact.

## Recommended immediate implementation sequence

1. Finish the **current exact commit** CI, fix failed tests if any. Keep both PR #143 (base) and PR #144 (V2 Goal continuation) in draft; do not merge to main or deploy.
2. Complete **original Drive source → normalized FLAC SHA provenance attestation** and on-VPS validation of private GCS bucket, cost estimator and local clip staging already implemented behind approval; prove safe cloud retries with exact object identity.
3. Connect the isolated Gemini stage executor to a **resumable V2 Worker** and existing V1 hardened asynchronous Chirp Dynamic/Standard submit/recovery. Prefer the new read-only Chirp PASS reuse path when source-matched. Add per-window cost accounting and asynchronous provider-operation reconciliation so partial multi-window charges are never hidden. No paid ASR until exact source, model and per-window cost are approved.
4. Validate the newly added local manual overlap-seam reconciliation on actual consented recordings; add independently authenticated reviewer evidence and complete source VAD/Coverage checks. Current CLI records operator attestation only and cannot prove audio was heard.
5. Route vetted comparison evidence into existing ChatGPT handoff, repair only verified omissions, and pass all Golden QA/Drive revision lock checks.
6. After owner approves each live smoke and the aggregate ≤US$10 test budget, do short cases, then benchmark real Mandarin/Taiwanese/scripture, followed by long audio regression.
7. Extend the implemented read-only owner task status API with reviewed, authenticated mutation operations, Skill integration and thin UI; create staged exact-SHA release; only then propose formal production migration.

## Mandatory next-agent report

Exact repo/ref/SHA and CI run IDs; dirty/live worktree state; provider calls and dollars spent; Drive mutated YES/NO; production mutated YES/NO; resumed provider evidence vs new attempts; QA PASS/BLOCKED and unresolved exact-gate IDs. For scope and owner approvals see `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`.
