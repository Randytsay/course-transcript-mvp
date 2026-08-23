# ASR Quality-Gated Retranscription Design

## Purpose

This branch supersedes the implementation direction from PR #11 while preserving the actual product requirement: identify suspicious ASR chunks and allow a targeted re-transcription instead of forcing a full-course rerun.

The redesign starts from current `main` and treats three concerns separately:

1. quality detection;
2. paid re-transcription execution;
3. candidate comparison/apply.

No detached API subprocess and no in-place overwrite of accepted transcript artifacts are allowed.

## Quality detection

A suspicious chunk must not be identified by one fixed word-count threshold alone. The scorer should combine deterministic signals available from the existing chunk artifacts, including where available:

- source duration;
- recognized text/character count;
- words count;
- first and last recognized word timestamps;
- recognized timeline coverage;
- longest uncovered interval;
- duplicate/repeated text ratio;
- density relative to the same course's median chunk density;
- density deviation from neighboring chunks;
- provider status / missing expected artifacts.

### Relative course baseline

The same course is the primary comparison population. A 15-minute chunk with 500 characters may be suspicious in a lecture where neighboring chunks contain 2,500-3,000 characters, but normal in a chunk dominated by silence or media playback.

The scorer therefore reports signals and severity rather than making a paid provider call automatically.

Suggested output per chunk:

```json
{
  "chunk_index": 4,
  "severity": "high",
  "reasons": ["density_below_course_baseline", "timeline_coverage_low"],
  "metrics": {
    "duration_ms": 900000,
    "char_count": 381,
    "course_median_char_count": 2702,
    "relative_density": 0.141,
    "timeline_coverage": 0.18
  }
}
```

## Explicit paid-operation gate

Quality detection is free/local. It may only mark a chunk as suspicious.

A Chirp re-transcription requires an explicit owner action after showing:

- job/course;
- chunk index and time range;
- current quality evidence;
- recognizer/configuration;
- estimated paid operation;
- current transcript revision.

No quality threshold may auto-submit a paid Chirp operation.

## Durable task model

A re-transcription request must be a durable database-backed task consumed by the pipeline worker.

Required idempotency identity:

```text
job_id
+ source_revision
+ chunk_index
+ source_audio_sha256
+ recognizer
+ recognizer_config_sha256
```

The worker must use lease/restart recovery semantics. Browser refresh, API retry, worker restart, or duplicate clicks must not submit the same paid operation twice.

## Immutable candidate outputs

A targeted rerun must never delete or replace the accepted chunk artifacts in place.

Conceptual layout:

```text
jobs/<job>/
  chunks/chunk-004/                 # accepted/original source artifacts
  retranscription-candidates/
    <candidate-id>/
      manifest.json
      words.json
      partial-transcript.json
      quality.json
      provider-evidence.json
```

The candidate records:

- source job/revision/chunk;
- exact source audio hash;
- recognizer/model/config;
- provider operation evidence;
- creation actor/time;
- candidate quality metrics;
- source/current comparison state.

## Compare / approve / apply

Completing a paid rerun creates only a candidate.

The owner workflow must support:

- replay source interval;
- original vs candidate text diff;
- original vs candidate metrics;
- keep original;
- apply candidate.

Apply requires optimistic revision checking. If the transcript/human-review source advanced after candidate creation, the candidate becomes stale and cannot silently overwrite newer work.

Applying a candidate creates a new transcript revision and rebuilds downstream derived artifacts from that revision. The original and candidate remain auditable.

## Separation from AI text correction

ASR retranscription and text correction are different operations:

```text
Audio -> Chirp 3 -> immutable ASR revision -> optional targeted re-ASR candidate
                                              -> approved ASR revision
                                              -> AI text correction router
```

MiniMax/Vertex/OpenRouter correction must never be used as proof that the audio was recognized correctly. They operate only after the ASR source revision is selected.

## Implementation phases

### Phase A — local quality scorer

- deterministic quality metrics from existing chunk artifacts;
- course-relative baseline;
- persisted/readable quality report;
- no paid calls;
- unit tests for silent/short/normal/outlier chunks.

### Phase B — durable rerun request

- DB task state;
- explicit approval request;
- idempotency key;
- pipeline-worker lease/recovery;
- no detached subprocess;
- no overwrite.

### Phase C — immutable candidate + comparison

- versioned candidate directory;
- compare API/UI;
- stale revision detection;
- apply creates a new revision;
- downstream rebuild only after apply.

### Phase D — VPS live gate

- one known suspicious chunk;
- duplicate-click/idempotency test;
- worker restart during queued/running state;
- candidate-vs-current comparison;
- stale-edit conflict test;
- explicit apply/rollback proof;
- proof that accepted artifacts remain unchanged before apply.

## Production boundary

This design document itself authorizes no paid request, VPS deployment, production DB mutation, or transcript overwrite. Each runtime phase must land behind tests and a separate live validation gate.
