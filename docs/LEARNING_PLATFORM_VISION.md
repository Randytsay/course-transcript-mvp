# 佛學共學平台 — Learning Platform Vision

Status: design baseline for the learning-platform workstream.

This document defines the product direction that extends the subtitle-review portal into a one-stop learning and review platform. It intentionally builds on the reviewer/auth/video/subtitle/version infrastructure instead of creating a second user system.

## Product promise

A signed-in learner should always be able to answer four questions within seconds:

1. 我正在學哪一堂？
2. 我上次看到哪裡？
3. 哪些內容我已經學完／還沒複習？
4. 這堂課有哪些可信、可追溯到影片時間碼的重點？

The platform combines:

- YouTube course playback;
- watch progress;
- explicit learning completion;
- subtitle-review contribution;
- bookmarks and personal notes;
- AI-generated study notes grounded in approved subtitle versions;
- quick review, quiz and flashcards;
- cross-video knowledge search with source timestamps.

## Progress semantics

Never conflate these dimensions:

- **觀看進度** — playback position and watch completion signal;
- **學習狀態** — learner explicitly marks a lesson as completed / in progress;
- **複習進度** — quiz/flashcard/review activity and next-review date;
- **共修進度** — subtitle review progress and contribution state.

Watching a video to the end must not automatically mean the learner understood it or reviewed its subtitles.

## Canonical-source rule for AI learning artifacts

Formal AI notes must be generated from an immutable subtitle version, not from an in-flight suggestion or mutable working text.

Every artifact stores at least:

- `youtube_video_id`;
- `subtitle_version_id`;
- `source_sha256`;
- `artifact_type`;
- `model`;
- `prompt_version`;
- `generated_at`;
- structured citations that point back to subtitle segment IDs / video time ranges.

If the approved subtitle version changes, older AI artifacts remain readable historical evidence but are marked stale until regenerated.

## First-class learning surfaces

### 我的學習中心

- continue-learning card;
- overall lesson completion;
- recently watched;
- not started / in progress / completed / saved;
- review due today;
- recent notes/bookmarks.

### Lesson workspace

Tabs:

- 影片＋字幕;
- AI 筆記;
- 重點;
- 問答;
- Flashcards;
- 我的筆記.

AI content must expose timestamp citations so a learner can jump directly to the source in the video.

### 複習中心

- 3-minute review;
- 10-minute review;
- flashcards;
- quiz;
- spaced-review queue (1 / 3 / 7 / 14 / 30 day default cadence).

### 知識搜尋

Search approved subtitle text and published AI artifacts across lessons. Results return the lesson, matching text, artifact/source type and a clickable video timestamp. Cross-video LLM synthesis is a later layer and must cite retrieved source ranges rather than answer from model memory alone.

## Safety and cost boundaries

- Reviewer-facing reads never trigger paid LLM generation automatically.
- AI artifact generation is owner/admin initiated or explicitly scheduled after cost policy is configured.
- Generation is idempotent per `(video, subtitle_version, artifact_type, prompt_version)`.
- Generation failure never invalidates the approved subtitle version.
- No learning action publishes or modifies YouTube captions.

## Delivery sequence

The learning-platform implementation should be stacked on top of Reviewer/Admin UX V2 until that PR merges.

1. Learning state, bookmarks and notes.
2. Learning dashboard + lesson workspace.
3. Version-grounded AI artifact storage and owner generation workflow.
4. Quiz/flashcards/review scheduler.
5. Cross-video search.
6. Optional semantic/RAG layer after enough canonical content exists.
