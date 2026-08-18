# 佛學共學平台 — Release Candidate acceptance contract

Status: code-complete handoff contract for stacked Draft PR #57.

This document defines what must be true before the one-stop learning platform can be called a merge candidate. It is deliberately stricter than a green unit-test run: browser behavior, reviewer identity, mobile usability and production isolation still need independent runtime evidence.

## Product journey that must work

```text
登入
  ↓
我的學習中心
  ├─ 知道哪些課尚未開始 / 學習中 / 已學完
  ├─ 從上次影片位置繼續
  ├─ 搜尋 / 篩選 / 排序課程
  ├─ 查看待複習 / 收藏 / 個人筆記 / 書籤
  └─ 看見字幕共修進度但不與學習完成混為一談
  ↓
單堂課
  ├─ YouTube + 同步字幕
  ├─ 收藏時間點
  ├─ 個人筆記
  ├─ AI 詳細筆記 / 3 分鐘 / 10 分鐘複習
  ├─ Q&A / Flashcards / 自我測驗 / 名詞整理
  ├─ 每個正式 AI 項目回到實際來源時間碼
  ├─ 主動標記「我已學完」
  └─ 需要時進入字幕共修
  ↓
複習中心
  └─ 1 / 3 / 7 / 14 / 30 天課程複習 + 單張 Flashcard 排程
  ↓
知識搜尋
  ├─ 課程字幕文字命中
  └─ 僅顯示目前有效 AI Study Pack，且命中項目使用自己的來源時間碼
```

## Learner guidance

Learners must never be expected to understand implementation terms such as lease, TTL, heartbeat, CSRF, SQLite or caption-track mechanics.

The platform provides two levels of help:

- contextual copy in `/review`, `/review/learn`, lesson, review and search surfaces;
- complete learner guide at `/review/help`, reachable from the learning navigation and a persistent one-tap help control under `/review/*`.

The learner guide explains:

1. how to begin a lesson and resume later;
2. bookmarks and personal notes;
3. how AI notes are grounded and how to return to the video source;
4. why playback completion is not the same as explicit learning completion;
5. spaced review;
6. subtitle共修 without timestamp editing or direct YouTube mutation;
7. the four distinct dimensions: playback position, learning completion, review progress and subtitle-review progress;
8. Google/LINE identity linking and cross-device continuity.

Owner guidance is separately available at `/review-admin/help` and must keep import, suggestion review, immutable versions, AI generation and YouTube publishing as separate operational gates.

## Learning-state semantics

These states must remain independent:

- **播放位置 / 觀看進度** — where the learner resumes the video;
- **學習完成** — explicit learner action; reaching the end must not set it automatically;
- **複習進度** — review schedule, quiz attempts and Flashcard schedule;
- **字幕共修進度** — reviewer progress and completion.

A learning watch heartbeat may update the shared resume position but must not clear an already-completed subtitle review. Rewatching an explicitly completed lesson must not silently reopen its learning state.

## Formal AI evidence contract

A learner read must never trigger a paid model call.

Formal Study Pack generation requires:

1. an immutable subtitle version;
2. explicit owner approval of that exact version as the formal learning source;
3. a separate explicit paid-generation confirmation.

Every stored Study Pack records the subtitle version ID, source SHA-256, model, prompt version, actor and generation time.

The model is untrusted structured input. The server must:

- reject unsupported source indexes;
- rebuild source timestamps/text from the immutable subtitle snapshot;
- discard malformed or unsupported Study Pack entries;
- require grounded overview/content;
- validate Flashcard IDs/content;
- validate quiz choices and answer indexes;
- avoid inventing content merely to fill a target count.

When a newer immutable subtitle version exists, the older artifact remains auditable but is stale. Stale quizzes/Flashcards must not accept new learning-progress mutations.

## Quiz and Flashcard integrity

Quiz scores are server-derived. A client-provided `score` / `total` is not authoritative and must not be able to inflate learning history.

Flashcard reviews must reference an actual card from the current, non-stale artifact. Arbitrary card keys or historical/stale artifacts must be rejected.

## Knowledge-search integrity

Subtitle search may surface the current course subtitle text and should not overclaim that every current working segment is already a formal immutable learning source.

AI search must:

- include only the current non-stale Study Pack for each lesson;
- search human-readable item content rather than expose raw JSON as the learner snippet;
- associate each hit with that item's own `source_segment_indexes`;
- resolve the video timestamp from the server-built citation for the matched item;
- never use the first citation of an artifact as a generic timestamp for unrelated matches.

The learner UI must state the distinction between current course subtitle search and version-grounded AI material.

## Interrupted paid generation

An ambiguous browser/network failure must not encourage a blind retry.

The owner UI tells the operator to refresh and inspect generation history first. Old `running` generation rows are recovered only after a bounded stale interval; recent running jobs continue to block duplicate generation. Recovery changes only generation-job status and never alters subtitle evidence, an existing Study Pack or YouTube.

## Responsive and accessibility baseline

Independent browser acceptance must cover at least:

- 390 px mobile;
- 768 px tablet;
- 1440 px desktop.

Check:

- no horizontal layout breakage or text clipping;
- Chinese course titles wrap safely;
- major touch controls are approximately 44 px or larger;
- visible keyboard focus;
- tab/tabpanel semantics in the lesson workspace;
- meaningful image/button labels;
- loading, empty and error/retry states;
- reduced-motion behavior does not break interaction;
- persistent help control does not cover primary controls;
- YouTube iframe remains usable on mobile.

## Runtime seed scenarios

Use isolated SQLite data only. Seed enough evidence to exercise:

- at least 3 lessons: not started, in progress and completed;
- watch position distinct from subtitle-review progress;
- one learner with notes/bookmarks;
- review due and not-due lessons;
- current AI artifact;
- stale AI artifact;
- quiz + Flashcards;
- reviewer pending/approved/rejected/withdrawn suggestions from PR #56;
- immutable subtitle baseline and later version;
- owner generation history with completed/failed/running rows;
- one intentionally abandoned old running generation row to prove recovery;
- AI search where two items cite different subtitle segments, proving each result jumps to its own evidence.

## Browser flows to accept

### Learner

1. Login through the real reviewer auth flow without changing production data.
2. Open `/review/help` and verify first-use guidance.
3. Open learning dashboard, search/filter/sort lessons and resume a lesson.
4. Play/seek and verify resume persistence.
5. Add/delete bookmark and note; verify cross-page persistence.
6. Mark lesson complete and confirm rewatch does not reopen it.
7. Confirm subtitle-review completion is unaffected by learning playback.
8. Use every lesson tab.
9. Click AI citation timestamps and verify the video seeks to the correct source.
10. Submit a quiz and prove the persisted score matches server grading.
11. Review a valid Flashcard; prove an arbitrary card key is rejected.
12. Complete a due review and verify next schedule.
13. Search the same phrase in subtitle and AI material; verify exact source jumps.

### Owner

1. Open `/review-admin/help`.
2. Verify latest subtitle / formal learning source / artifact state.
3. Approve a learning source only in isolated data.
4. Mock/block the paid model and verify generation confirmation.
5. Verify a recent running generation blocks a duplicate.
6. Verify an intentionally stale running row is failed before a later explicit retry.
7. Verify stale source/version prevents generation.
8. Verify learner reads never trigger generation.

## Non-negotiable external-write boundary

Acceptance must perform **no real external mutation**:

- no production DB write;
- no production service restart/recreate;
- no full-playlist import;
- no real paid AI generation;
- no `captions.update`;
- no YouTube publish/republish/rollback;
- no change to Google, LINE, Cloudflare or YouTube OAuth configuration.

Mock/block the model and every YouTube write path in isolated browser/runtime testing.

## Merge order

PR #57 is stacked on PR #56.

The intended order is:

1. independently accept exact PR #56 head;
2. merge PR #56 only after owner approval;
3. rebase/retarget PR #57 onto the resulting `main` and resolve only the stacking delta;
4. rerun all CI and isolated runtime/browser acceptance on the new exact PR #57 head;
5. merge only after separate owner approval;
6. production deployment remains another exact-SHA gate.

Do not deploy a stacked Draft head directly to production.

## AI-agent result format

Return at least:

```text
LEARNING PLATFORM RELEASE CANDIDATE ACCEPTANCE

Exact PR #56 SHA:
Exact PR #57 SHA:
Git status:
Secret scan:
Python focused tests:
Python full suite:
Frontend tests:
Next build:
Dependency audit:
Compose:
Release Compose:
ARM64 app image:
ARM64 frontend image:
Revision labels:

390px:
768px:
1440px:
Console errors:
Unexpected 4xx/5xx:

Learner help:
Learning dashboard:
Resume position:
Learning completion independence:
Subtitle-review progress independence:
Notes/bookmarks:
AI citation jumps:
Quiz server grading:
Flashcard validation:
Spaced review:
Knowledge search exact citation:

Owner help:
Source approval separation:
Paid generation blocked/mocked:
Recent duplicate generation blocked:
Stale generation recovery:
Stale artifact behavior:

Real paid model call performed: NO
Real YouTube write performed: NO
Production modified: NO

Overall: PASS / FAIL
Blocking issues:
Recommended next action: MERGE_CANDIDATE / FIX_REQUIRED
```

Even `MERGE_CANDIDATE` is not permission to merge or deploy.
