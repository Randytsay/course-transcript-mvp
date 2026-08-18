# Learning Platform implementation and acceptance plan

Status: implementation contract for stacked Draft PR #57.

Base: Reviewer/Admin UX V2 (`feat/reviewer-admin-ux-v2`).

This work turns the subtitle-review foundation into a one-stop Buddhist learning and review platform without creating a second identity/video/version system.

## Delivered architecture

### Shared evidence and identity

The learning domain reuses:

- `review_users` for the logical learner/reviewer identity;
- `review_videos` for the course catalog;
- `review_video_progress.last_playback_ms` for one canonical resume point;
- `review_subtitle_segments` for the current owner-approved working subtitle;
- `review_subtitle_versions` for immutable subtitle evidence.

No learning action changes subtitle timing or publishes YouTube captions.

### New learning persistence

All additional state is isolated under `learning_*` tables:

- `learning_video_state` — explicit learning state and saved lessons;
- `learning_bookmarks` — timestamp/segment bookmarks;
- `learning_notes` — learner-owned notes;
- `learning_source_versions` — owner-pinned formal subtitle source;
- `learning_artifacts` — version-grounded AI content;
- `learning_generation_jobs` — paid-generation audit/status;
- `learning_review_schedule` — lesson spaced-review cadence;
- `learning_quiz_attempts` — learner quiz history;
- `learning_flashcard_progress` — per-card review state.

SQLite WAL remains the storage model. Schema initialization is additive and idempotent.

## Learner API boundary

Prefix: `/api/v1/review/learning`

All reads require the existing reviewer session. All mutations additionally use the existing reviewer Origin/CSRF gate.

Supported operations:

- learning dashboard;
- lesson workspace data;
- explicit learning status / saved state;
- watch/resume position;
- notes/bookmarks;
- review queue and review completion;
- quiz attempt recording;
- Flashcard review scheduling;
- grounded cross-video search.

A learner read never invokes an LLM.

## Owner AI boundary

Prefix: `/api/v1/review-admin/learning`

This remains inside the existing owner/Cloudflare Access boundary.

Formal AI generation is deliberately two-step:

1. owner explicitly approves the current immutable subtitle version as the **formal learning source**;
2. owner separately confirms the paid Study Pack generation request.

A newer subtitle version does not silently become the AI source. The prior source remains auditable until the owner explicitly re-approves the newer version.

## Study Pack contract

One Study Pack can include:

- overview;
- detailed notes;
- 10-minute review;
- 3-minute review;
- key points;
- Q&A;
- Flashcards;
- self-test quiz;
- glossary.

Every supported item includes `source_segment_indexes`. Model-provided indexes are validated against the immutable snapshot; unsupported indexes are discarded. Timestamp/text evidence is rebuilt by the server from the source version and is never trusted from model output.

Artifacts persist:

- source subtitle version ID;
- source SHA-256;
- artifact type;
- model;
- prompt version;
- generation actor/time;
- normalized content;
- source citations.

## Learning UX

### `/review`

Combined 佛學共學 landing page while retaining subtitle共修 safety guidance and Google/LINE reviewer login.

### `/review/learn`

Personal learning center:

- continue from last position;
- explicit lesson completion;
- not started / in progress / completed / saved;
- review due today;
- recent notes/bookmarks;
- AI-content availability.

The UI keeps these dimensions distinct:

- 觀看進度;
- 學習完成;
- 複習進度;
- 字幕共修進度.

### `/review/learn/{videoId}`

One-stop lesson workspace:

- video + subtitle;
- AI study notes;
- quick review;
- Q&A;
- Flashcards;
- quiz;
- personal notes/bookmarks;
- jump-to-source timestamps;
- direct entry to subtitle共修.

### `/review/learn/review`

Review queue using default 1/3/7/14/30-day lesson cadence. Flashcards have an independent per-card schedule.

### `/review/learn/search`

Grounded textual search across the current owner-approved working subtitle and stored version-grounded learning artifacts. Search results link back to source timestamps. Generative cross-video synthesis is intentionally not part of M1 until a learner-facing cost/rate policy exists.

### `/review-admin/learning`

Owner console for:

- latest immutable subtitle version;
- formal learning-source status;
- artifact current/stale/missing state;
- source approval;
- explicit paid generation;
- generation audit/status;
- learner-view preview.

## Validation gates

### GitHub CI

Required on the exact PR head:

- Python compile/import;
- complete Python test suite;
- frontend behavior tests;
- Next.js production build;
- production dependency audit;
- Compose/release validation;
- app/frontend Docker builds;
- immutable revision-label verification.

### Independent AI-agent/local gate

Before merge:

1. checkout exact head and prove clean status;
2. run focused learning/reviewer tests and full suite;
3. run frontend tests/build;
4. scan added code for credentials/secrets;
5. run an isolated stack with a temporary SQLite DB;
6. seed multiple learners/videos/progress states and immutable subtitle versions;
7. mock/block external LLM and every YouTube write path;
8. browser-smoke `/review`, learning center, lesson, review center, search and learning admin at 390/768/1440 widths;
9. verify console/network errors, keyboard/touch usability and long Traditional Chinese content;
10. verify reviewer CSRF and owner Cloudflare Access boundaries;
11. verify a learner cannot invoke admin generation;
12. verify source approval and AI generation are two separate owner actions;
13. verify no learner read triggers a model call;
14. verify no learning path calls `captions.update`;
15. prove production DB/containers and YouTube were not modified.

## Production gate

Passing PR/agent validation is not deployment authorization.

A future deployment requires:

- PR #56 merged first;
- this stacked PR rebased/retargeted to the approved main SHA;
- exact-SHA immutable release build;
- verified SQLite backup before additive schema initialization;
- targeted API/frontend cutover only after owner approval;
- post-cutover auth/health/browser acceptance;
- first real AI Study Pack only after the owner separately approves a formal subtitle source and the paid generation request.
