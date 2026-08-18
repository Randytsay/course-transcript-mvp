# AI learning artifacts — trust, provenance and regeneration

Formal learning artifacts are version-grounded and citation-backed. Generation is owner controlled and does not mutate YouTube captions.

## Trust chain

```text
YouTube / imported subtitle
        ↓
review suggestions
        ↓
owner approval / batch correction
        ↓
immutable review_subtitle_versions row
        ↓
owner explicitly approves one version as learning_source_versions
        ↓
explicit paid Study Pack generation
        ↓
learning_artifacts + server-built citations
```

A mutable working subtitle, pending suggestion or raw model response is never considered formal learning content by itself.

## Source approval is separate from generation

Two separate owner decisions are intentional:

1. **核定學習來源** answers: “Is this exact immutable subtitle version ready to become learning material?”
2. **產生 AI 學習整理** answers: “Do I want to spend model quota and create/update the Study Pack now?”

This prevents a freshly imported baseline or an accidental intermediate version from automatically becoming a public learning source.

## Artifact identity

Artifacts are idempotent on:

```text
(youtube_video_id, subtitle_version_id, artifact_type, prompt_version)
```

A stored artifact records:

- `subtitle_version_id`;
- `source_sha256`;
- `artifact_type`;
- normalized JSON content;
- citation evidence;
- model;
- prompt version;
- actor;
- generation time.

A duplicate non-forced request for the same source/prompt returns the current artifact without another model call.

## Staleness

Historical artifacts are not deleted when subtitles change.

If a newer immutable subtitle version exists:

- the previously approved formal source stays pinned;
- the owner console says the formal source is no longer the latest version;
- the prior artifact remains auditable;
- the owner must explicitly re-approve the new subtitle version;
- the artifact can then be regenerated against that new source.

This preserves reproducibility instead of silently rewriting learning material.

## Citation contract

The model never supplies trusted timestamps.

It may only reference `source_segment_indexes` that were included in the prompt. The server then:

1. validates every index against the immutable snapshot;
2. discards unsupported indexes;
3. reconstructs `start_ms`, `end_ms` and source text from the snapshot;
4. stores those citations with the artifact.

Formal items without a valid source index are discarded. This keeps clickable “回到影片” references deterministic and prevents fabricated timecodes.

## Prompt policy

The Study Pack prompt requires the model to:

- use only the approved subtitle source;
- avoid adding doctrine, people, scriptures or facts that are not in that source;
- omit unsupported claims rather than guess;
- keep Buddhist terminology faithful to the source;
- create review material for understanding, not trick questions.

The server still treats all model output as untrusted structured data and normalizes its fields/lengths before storage.

## Generation failure

A failed model call:

- creates/updates a generation-job failure record;
- does not alter the immutable subtitle version;
- does not alter the approved formal learning-source pointer;
- does not claim an artifact was successfully generated;
- never publishes YouTube captions.

## Cost boundary

Learner-facing GET requests never trigger generation.

Generation lives only under the owner/admin API and requires an explicit confirmed mutation. A future learner-facing generative Q&A/RAG feature must introduce its own quota/rate/cost policy rather than piggyback on normal reads.

## YouTube boundary

Learning APIs and AI artifact generation have no authority to call `captions.update`.

YouTube subtitle publishing remains a separate owner workflow in the subtitle administration subsystem. Learning content may link back to a YouTube timestamp but must never modify the remote caption track.
