# Golden V2 integration handoff — non-paid durable-stage milestone

> Date: 2026-10-12 (Asia/Taipei)  
> Branch: `feat/golden-v2-gemini38-primary` (PR #143, **draft; NOT merged, NOT deployed**).  
> Work in this document is an isolated engineering scaffold, **not a runnable replacement for the production Chirp-first Worker**.

## Implemented components (actual code paths)

| Layer | Code | Contract |
|---|---|---|
| Primary recognition | `app/providers/gemini_primary_audio.py` | Gemini 3.8 audio-first Vertex request adapter, GCS window plan/identity, immutable raw response, truncated/rejected response review; never auto-repeat unknown paid call |
| Existing correction target | `app/providers/correct_text.py`, `correct_text_cascade.py` + related API/Worker/Proxy/console defaults | V2 branch replaces 3.7 target with 3.8; historical V1 raw records must stay unchanged |
| Independent acoustic comparison | `app/providers/golden_v2_compare.py` | Gemini/Chirp normalized text disagreement, original Chirp evidence/timestamps only; **not** speech coverage or correctness proof |
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
3. **P2:** Primary audio can be staged on GCS by a future worker but **actual source-to-GCS audio normalization/chunk extraction, attested source SHA, exact overlap audio/text stitching** are not yet connected. Long overlapping windows fail closed rather than silently producing duplicate/omitted text.
4. **P3:** V1 hardened Chirp asynchronous Dynamic Batch/Standard bounded repair and VAD/Coverage must be wired into V2 *after* Gemini primary; independent merged word timing and completion gates remain mandatory. Current comparison is only text-based.
5. **P4:** Real Vertex 3.5 Transcribe SDK/endpoint smoke and provider usage pricing are not complete. Per current Vertex documentation, diarization+vocabulary is illustrated as supported; word_timestamp+vocabulary is conservatively kept separate. Taiwanese/Hokkien specific performance is **not yet verified**; test with real gold recordings. Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-transcribe
6. **P5:** Actual ChatGPT/WebCodex audio evidence adjudication and handoff imports remain to implement; no automatic difference resolution exists.
7. **P6:** No trusted benchmark dataset or actual bill yet; current CER is only a calculation library.
8. **P7:** SQLite stage claim and provider adapter authorization are now tested together with mock clients; no owner-only HTTP endpoint or continuously running VPS V2 worker has been deployed. Crash/outcome-unknown stays blocked until independent evidence verifier approves reconciliation. Only the read-only status CLI exists.
9. **P8:** UI and official Drive delivery hook are not integrated; final QA verdict does **not** perform publication. Existing V1 safe-publish remains intact.

## Recommended immediate implementation sequence

1. Finish the **current exact commit** CI, fix failed tests if any. Keep PR #143 draft; do not merge to main or deploy.
2. Add a trusted V2 source-audio preflight/staging service: SHA verification, bounded normalized clip staging to private GCS, matching original source clock and request estimates.
3. Build a durable, resumable V2 worker that uses SQLite `claim_next` and the existing V1 hardened Chirp operation/recovery, with stage-level and window-level idempotency, **no paid ASR until exact source and cost are approved**.
4. Add deterministic *overlap-aware Gemini primary text reconciliation* with audio anchor review, source VAD/Coverage, and fail-closed primary completion before Chirp.
5. Route vetted comparison evidence into existing ChatGPT handoff, repair only verified omissions, and pass all Golden QA/Drive revision lock checks.
6. After owner approves each live smoke and the aggregate ≤US$10 test budget, do short cases, then benchmark real Mandarin/Taiwanese/scripture, followed by long audio regression.
7. Add owner-facing task API/Skill operations and thin UI; create staged exact-SHA release; only then propose formal production migration.

## Mandatory next-agent report

Exact repo/ref/SHA and CI run IDs; dirty/live worktree state; provider calls and dollars spent; Drive mutated YES/NO; production mutated YES/NO; resumed provider evidence vs new attempts; QA PASS/BLOCKED and unresolved exact-gate IDs. For scope and owner approvals see `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`.
