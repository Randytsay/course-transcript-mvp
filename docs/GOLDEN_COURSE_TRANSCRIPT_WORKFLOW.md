# Golden Course Transcript Workflow

Canonical operator/AI contract: see
`skills/golden-course-transcript/SKILL.md`.

The contract intentionally composes existing production primitives rather than
creating a second transcription pipeline. Historical/multi-course folders first
pass a provider-free inventory and lineage gate:

`Drive discovery -> Media Inventory/Dedup Dry Run -> deterministic topic classification/profile routing -> existing job reconciliation -> Chirp 3 evidence ->
Completeness Gate/targeted repair -> CHATGPT_HANDOFF -> deterministic cleanup /
Golden Rules / rendering -> hardened validation -> safe Drive publication`.

The inventory gate uses Drive metadata/MD5 plus existing sidecars to identify
exact duplicates and conservative master/segment/container/alternate/derived
candidates. Ambiguous lineage is `VERIFY_DUPLICATE` and blocks paid ASR until
local duration/audio-fingerprint verification confirms the relationship.

`VERIFY_DUPLICATE` is resolved by the local-only lineage verifier before any
costed preflight. It downloads only the ambiguous media set one file at a time,
records FFprobe duration plus FFmpeg Chromaprint evidence in a resumable local
cache, and deletes each temporary source after fingerprinting. Confirmed
same-timeline, segment-of, and derived-from relations reuse one canonical media
identity. Ambiguous/error results remain blocked; low-similarity evidence never
silently becomes a duplicate. The verifier does not call Chirp/Gemini and does
not mutate Drive.

For `market_america_training`, the fixed Chirp subtitle layer also triggers one
read-only ShopClaw terminology snapshot before model correction/handoff. The
snapshot is spelling evidence only and intentionally excludes prices, raw
catalog copy and unsupported claim expansion.

For historical Market America course migration, an SRT is not considered
**Golden** merely because ASR/export/Drive publication completed. Golden promotion
requires a provider-independent quality pass over every subtitle-bearing job:

1. Acoustic coverage audit over the complete source audio. Audible/VAD-confirmed
   subtitle gaps and audible uncovered tails are blockers.
2. Cross-file lineage plus transcript-overlap audit so masters, segments,
   alternate recordings and derived clips do not silently duplicate knowledge.
3. Market America terminology evidence review using the read-only ShopClaw
   snapshot. It may correct spelling/identity only when audio/context supports
   the correction; it must never expand health, dosage, income or efficacy claims.
4. Same-course presentation evidence inventory (PDF/PPT/images/text). Timestamped
   slide captures should be aligned to the corresponding media time when
   available and used as spelling/context evidence, never as timing truth.
5. Only after these gates pass may a transcript receive a Golden marker and
   replace the previously published SRT. Existing Drive SRTs are preserved with
   the operator's old-version suffix policy before promotion.

Provider-free audit reports must explicitly retain
`provider_calls_started=false` and `drive_mutation_started=false`.

### Default execution strategy

The production default is deliberately split by workload:

- **Bulk first-pass ASR:** use `DYNAMIC_BATCHING` for canonical media only.
- **Any repair/retry:** use **Standard Batch** for the specific missing chunk or
  bounded gap. Never send an already-successful chunk back through the provider.
- **Provider terminal/no-output:** stop polling the dead operation. Archive the
  attempt and create at most one fresh Standard repair attempt for that missing
  chunk under the approved batch budget. The retry must receive a fresh patch
  identity and provider operation/GCS prefix; stale submitted/recovery markers
  may never route the replacement window back to the terminal operation.
- **Coverage / VAD / QA:** local and provider-free. Density anomalies alone are
  review warnings; they become blockers only when corroborated by audible gaps,
  broken timing/provider evidence, or an uncovered audible tail.
- **Audio retention:** keep normalized 16 kHz mono audio until Completeness has
  passed (or the job is intentionally parked for handoff/review). Do not delete
  it immediately after ASR if downstream repair may still be required.
- **Canonical-first:** duplicate/segment/alternate media inherit canonical
  evidence wherever lineage is confirmed; do not repeat full Golden QA on the
  same content identity.
- **Batch-level repair reserve:** repair authorization is evaluated against the
  already approved batch reserve before falling back to a per-job reservation,
  so one underestimated job cannot stall a still-within-budget batch.
- **Bounded multi-round repair:** keep the per-round paid repair duration cap
  (default 10 minutes). If several repairable gaps exceed the aggregate cap,
  repair the safe subset first and defer the remainder to the next Coverage
  round. A single repair window above the cap still blocks.
- **Local recheck must not resubmit ASR:** when merged words already exist and a
  job is resuming from segment/Coverage/QA/validation, resume the local
  post-ASR pipeline directly.
- **Patch-reconstructed base chunks:** a failed base/retry chunk may be accepted
  in derived merge/validation only when successful patch evidence completely
  covers its original source interval. Raw failed provider evidence remains
  immutable and auditable.

This split is the default for future jobs, not a one-off migration override.

Version: **1.5**

This document is the stable handoff pointer for future ChatGPT/WebCodex sessions.
Changes to timing truth, completeness fail-closed behavior, Drive publication
safety, or evidence retention require a version bump and regression tests.

