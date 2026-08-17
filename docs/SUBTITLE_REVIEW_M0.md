# Subtitle Review M0

## Purpose

M0 defines the persistence and identity contract for a lightweight collaborative
subtitle-review experience for the Buddhist YouTube lecture collection. It is a
new collaboration layer beside the existing transcription/subtitle pipeline; it
does not change Chirp timing, Gemini/M3 correction evidence, Drive delivery, or
current production publish gates.

The target scale is intentionally small: tens of videos, many eligible reviewers
but only a handful of simultaneous active users. The existing SQLite database in
WAL mode remains the persistence engine.

## Product decisions frozen in M0

- Reviewers may sign in with either Google or LINE on desktop, tablet, or mobile.
- First successful login creates an active reviewer automatically. There is no
  invite code or owner approval step for account activation.
- A logical reviewer may explicitly bind both a Google identity and a LINE
  identity. Email equality never silently merges accounts.
- Reviewer-facing UI will be separate from the existing operator/transcription
  backend and will expose only the simple review workflow.
- Uploaded YouTube caption timing is authoritative for imported review segments.
  Reviewers suggest text changes; M0 never allows a suggestion to change timing.
- A submitted suggestion is append-only contribution evidence and counts toward
  the contribution board immediately, before owner approval.
- Revising the text of the same pending suggestion updates that suggestion and
  its changed-character count; it does not add a second submitted-suggestion
  credit.
- Submitting a suggestion does not mutate the segment's `working_text`. A later
  owner-review milestone will apply approved suggestions into versioned working
  subtitles.
- Progress is per logical reviewer and video so a reviewer can continue on
  another device or with another bound login provider.

## SQLite boundary

M0 shares `/app/data/course-transcript.db` but owns only tables prefixed with
`review_`. Existing job, provider, subtitle-editor, billing, and delivery tables
remain untouched.

### `review_users`

One logical human reviewer.

- `id`: internal stable ID
- `display_name`, `avatar_url`
- `role`: `owner` or `reviewer`
- `status`: `active` or `suspended`
- login/audit timestamps

### `review_auth_identities`

External sign-in identities bound to a logical reviewer.

- provider: `google` or `line`
- provider subject: stable provider identifier (`sub` / LINE user ID)
- optional email for display/contact only
- at most one identity per provider for one logical reviewer
- the same provider identity cannot belong to two reviewers

### `review_videos`

YouTube review source metadata.

- YouTube video ID
- playlist ID
- title and duration
- selected caption-track ID, language and name

M0 stores the identifiers but does not yet call YouTube APIs. Playlist/caption
synchronization belongs to a later milestone.

### `review_subtitle_segments`

Fixed imported cue boundaries.

- video and ordered segment index
- `start_ms`, `end_ms`
- `original_text`: initial imported YouTube text
- `working_text`: owner-approved working version (initially equal to original)
- segment revision

Initial import is deliberately fail-closed if a video already has segments. A
later versioned refresh flow must explicitly reconcile a changed YouTube caption
instead of silently destroying contribution history.

### `review_suggestions`

One reviewer-submitted correction proposal.

- segment and reviewer IDs
- segment revision at submission time
- original working-text snapshot
- suggested text
- human-oriented changed-character count
- status: `pending`, `approved`, or `rejected`
- review metadata reserved for the owner-review milestone

### `review_suggestion_events`

Append-only suggestion history such as `submitted` and `revised`. This is the
answer to "who changed what and when" even if the current suggestion text later
changes.

### `review_video_progress`

Per-reviewer continuation state.

- last playback position
- explicit reviewed-through boundary
- last segment index
- completed flag

Playback may move backward or forward. The reviewed-through boundary only moves
forward unless a future explicit reset operation is designed.

## Contribution semantics

The contribution board is based on submitted work, not owner approval.

- **Submitted corrections**: count of `review_suggestions` rows.
- **Changed characters**: sum of the current `changed_chars` value for those
  submitted suggestions.
- **Videos contributed**: distinct videos with at least one submitted suggestion.
- **Completed reviews**: videos the reviewer explicitly marked completed.

`changed_char_count()` is human-oriented. A one-character correction such as
`今` -> `經` counts as one character, not two edit operations.

This model intentionally recognizes reviewer effort immediately while preserving
a separate owner-controlled publication workflow.

## M1 API/auth direction

M0 intentionally does not implement provider handshakes. M1 should add a reviewer
session boundary separate from the current Cloudflare Access operator boundary.
Likely reviewer endpoints are:

- `GET /review/login`
- `GET /review/auth/google/start` and callback
- `GET /review/auth/line/start` and callback
- `POST /api/v1/review/identities/link`
- `GET /api/v1/review/me`
- `GET /api/v1/review/videos`
- `GET /api/v1/review/videos/{video_id}/segments`
- `POST /api/v1/review/segments/{segment_id}/suggestions`
- `PATCH /api/v1/review/suggestions/{suggestion_id}`
- `PUT /api/v1/review/videos/{video_id}/progress`
- `GET /api/v1/review/contributions`

Reviewer routes must not inherit the existing operator mutation gate by simply
turning Cloudflare Access off globally. The two trust boundaries should coexist.

## Later milestones

1. Google + LINE OAuth/OIDC session implementation.
2. Import one owned YouTube video and its uploaded caption track.
3. YouTube IFrame player + synchronized subtitle list.
4. Pause/edit/replay/resume reviewer interaction.
5. Cross-device continuation and reviewer home page.
6. `我的發心` and `發心功德榜` UI.
7. Owner approval/conflict workflow.
8. Cross-video search and batch replacement suggestions.
9. Playlist synchronization for the full collection.
10. Versioned full-caption YouTube publish, download verification, and rollback.

## M0 acceptance criteria

- Existing transcription tables and artifacts are untouched.
- Database initialization is idempotent and keeps WAL/busy-timeout behavior.
- First login identity resolution returns an active reviewer.
- Explicit Google/LINE identity binding resolves to one logical reviewer.
- Subtitle initial import preserves fixed timing and refuses destructive reimport.
- Suggestions count immediately without mutating the working subtitle.
- Revising a pending suggestion does not increment suggestion count.
- Resume state is stored per reviewer/video.
- Contribution totals can be derived from source records without a denormalized
  counter table.
