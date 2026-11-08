# Golden V2 P5 — ChatGPT／WebCodex evidence-grounded Golden QA (2026-10-13)

**Authority:** owner-approved design decision / development-only implementation; **NOT** evidence of production deployment, completed Golden QA, or audio certification. See `DECISIONS.md` and `docs/GOLDEN_V2_GATE_STATUS.md`.

## Approved role division

1. **`gemini-3.8-flash` (Vertex AI):** primary *verbatim* semantic transcript and distinct text-only correction adapter. The first-pass audio recognizer and later text-only editor must remain separate; no references may introduce unspoken text.
2. **`chirp_3` (Speech V2):** independent acoustic recognition, word timing, original audio coverage/repair evidence; **sole primary timing authority for SRT**. Do not synthesize word-level timestamps from text or interpolate unsupported utterances.
3. **`gemini-3.5-transcribe-preview` (Vertex AI):** **optional** independent third opinion only for difficult fragments/benchmarking. Two earlier `gemini-3.5-transcribe` GA submissions had unclear/404 outcomes and must retain distinct immutable claims. Never silently substitute model IDs or automatically invoke a third paid model.
4. **ChatGPT:** QA supervisor/semantic adjudicator; inspect evidence, scripture vocabulary, spelling, punctuation, proposed transcript corrections and severity, preserving reasoning/evidence and dissent. A ChatGPT judgment of fluent text is **not** acoustic verification.
5. **WebCodex:** execute bounded engineering/analysis, compare models, extract verified audio windows, drive automated QA tests and produce artifacts; never claim to have heard audio when it only read text. For actual acoustic listening, use validated audio-aware tooling and/or trusted human review.
6. **VPS Durable Worker:** durable queue, atomic stage claims, costs, checkpoints, leases/timeout, safe restart, bounded repair. A chat closing does not itself continue work; only deployed worker jobs do. Web UI and ChatGPT Skill must share **the same** ledger/API, not separate workflows.
7. **Owner:** only critical `HUMAN_REQUIRED` acoustic ambiguities and final Drive/publishing authorization. Previously recorded segment preferences are not a line-by-line certified gold reference.

## Desired modes (future production policy, **not yet switched**)

- **STANDARD_2WAY (recommended initial default):** Gemini 3.8 primary + Chirp 3 acoustic/timing, full local completeness and QA; no third model by default.
- **SMART_2PLUS:** selectively invoke a separately authorized 3.5 Preview on high-risk, bounded windows only **if supported by live endpoint capability and benchmark**. Verify window minimums/model input limits before enabling.
- **QUALITY_3WAY:** all three independently on a separately approved source for representative benchmarks/high-importance recitations. Run only when exact source, pricing, provider entitlements, budget and owner authorization are present. No automatic paid replay.

For the four-minute Buddhist sample the owner's qualitative **text preference** is `Gemini 3.8 > Chirp 3 > Gemini 3.5 Preview`, **not a general CER/WER ranking**. Normalized text similarity is not accuracy. Gemini 3.5 Preview succeeded genuinely for 240s with 813 chars, while 3.8 had 910 chars and Chirp 824 chars/710 word-timing pieces. Successful-model metered application estimate totals US$0.10, not final Cloud invoice; earlier two failed GA attempts still have unknown final billing.

## Golden QA evidence and decision lifecycle

```
SOURCE_SHA + immutable real provider receipts + audio + owner decisions
 → deterministic completeness, acoustic/VAD coverage and no invented timing
 → sentence/phrase/word alignment (3.8 semantics → Chirp word spans)
 → classify disputes: omission, hallucination, scriptures, people, audio, punctuation, timing
 → AUTO_PASS | AI_REVIEWED | HUMAN_REQUIRED (classification, not publication authority)
 → ChatGPT/WebCodex propose text-only modifications with evidence, never overwrite raw
 → Chirp-grounded SRT and TXT rendered from a **single corrected timed transcript**
 → golden-qa-report.json and adjudication.json (immutable hashes + unresolved blockers)
 → trusted owner final approval → separate Drive version-lock/backup/publish transaction
```

- `AUTO_PASS`: alignment/acoustic gate actually passes, no material lexical conflict, deterministic evidence present. No implication of owner authorizing publication.
- `AI_REVIEWED`: evidenced candidate repair with reason, source, original words, allowed vocabulary variants and excerpt range; never infer word timestamps.
- `HUMAN_REQUIRED`: unclear spoken words, high-impact Buddhist terminology, acoustic gaps, unclear source identity or conflicting references.
- Every `GOLDEN_PASS` requires **zero unresolved blockers**, authenticated reviewer/owner approval and independently verified audio coverage; 12/12 UI model preferences alone do not suffice.
- A successful provider request is never re-submitted to resolve downstream SRT, Drive or QA failures.

## Actual four-minute no-charge P5 rehearsal — newly implemented

Code: `app/pipeline/golden_v2_p5_evidence_trial.py`; tests: `tests/test_golden_v2_p5_evidence_trial.py`.
Pure Python offline command `python -m app.pipeline.golden_v2_p5_evidence_trial` accepts **real 240s FLAC, 3 immutable provider receipt bytes, 12-section owner JSON, raw Chirp SRT, owner-corrected SRT and a new empty output directory**. It:
- Requires audio/source SHA to match all three successfully completed provider receipts and owner selection.
- Verifies third Preview receipt binds previous two receipt SHAs, and Chirp evidence provides real word timestamps. It explicitly checks the `chirp_3` timing authority.
- Rejects edits to Chirp cue order, indices, start/end time, or a change in number of cues; rejects **unapproved lexical changes**. Only the owner-confirmed wording `所以比如有長行` may replace `所以這裡有長行的` in the existing excerpt, with punctuation normalized.
- Generates create-only `golden-candidate-REVIEW_ONLY.srt`, `golden-candidate-REVIEW_ONLY.txt`, `engine-comparison.json`, `adjudication.json`, `golden-qa-report.json`. The TXT is rendered from that **same** timed candidate; not silently copied from the Gemini 3.8 main transcript.
- Flags time gaps as **unknown speech**, not missing audio. Without independent VAD/full-audio listening and word-level semantic alignment, `golden_pass=false`, `safe_publish_allowed=false` and `status=REVIEW_REQUIRED` by design. Never presents this candidate as the full semantic Gemini/Chirp merge.

**Actually exercised on user's real 4-minute sample**: three raw cloud receipt SHAs (3.8 `6786bcb4…`; Chirp `b17a52f0…`; Preview `2d56ab1e…`) matched source SHA `17db78ae…`. All **10 targeted Python unit tests PASS**. Produced **60 timed candidate cues**, checked **710 Chirp word offsets**, preserved **12/12 owner choices** and one exact confirmed correction. Found **12 pauses >1.5s**, which are *possible review windows*, **not verified speech omissions**. In this rehearsal there are **five outstanding QA blocker classes**; `REVIEW_REQUIRED`, **not Golden PASS**. No paid inference/Drive mutation/V1 code changes.

### Command (requires original private local evidence; do NOT commit audio/receipts)

```bash
python -m app.pipeline.golden_v2_p5_evidence_trial \
  --audio /private/path/original-4min.flac \
  --primary /private/path/real-primary-receipt.json \
  --chirp /private/path/real-chirp-receipt.json \
  --third /private/path/real-preview-receipt.json \
  --owner-review /private/path/owner-listening-12-of-12-confirmed.json \
  --chirp-srt /private/path/chirp3-clip-relative-DRAFT.srt \
  --reviewed-srt /private/path/chirp3-clip-relative-人工確認長行-DRAFT.srt \
  --out /private/new/immutable-review-dir
```

## Unfinished P5 gates

1. Integrate this isolated offline rehearsal with **the existing** `app/pipeline/golden_v2_stage_runner.py`, durable ledger and `app/subtitles/golden_v2_render.py`; do NOT construct a second concurrent production QA ledger.
2. Verify authentic continuous waveform coverage using VAD against Chirp words; local comparison currently cannot certify silent gaps, missed utterances, misheard lecture context or approximate word boundaries.
3. Perform **real audio-grounded** span alignment for Gemini 3.8/Chirp and the optional third model; propose only source-backed lexical fixes and archive all before/after diffs.
4. Rebuild natural cue segmentation from genuine Chirp word spans; do not keep existing poor SRT line wrapping merely because timestamps parse.
5. Enforce authenticated final human review, no-blocker Golden Gate, TXT/SRT equality, Drive revision conflict and safe publication/rollback before any release.
6. Test long audio, multi-course diverse ground-truth clips, interruptions, duplicate jobs, billing settlement; G0–G8 formal acceptance still not complete.
