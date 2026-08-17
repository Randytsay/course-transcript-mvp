# Subtitle Review M2 — reviewer video workspace

M2 turns the M0/M1 identity and persistence foundation into an actual reviewer
workspace. It is intentionally stacked on M1 and does not add YouTube channel
owner credentials or caption publishing yet.

## Reviewer flow

```text
/review
  -> authenticated account
  -> /review/videos
  -> choose / resume a video
  -> /review/videos/{videoId}
  -> watch YouTube + synchronized fixed subtitle segments
  -> optionally acquire an edit seat
  -> submit or revise subtitle suggestions
  -> progress persists independently of edit-seat ownership
```

Watching never consumes an edit seat. A reviewer must explicitly select
`開始校訂` before subtitle mutations are accepted.

## Two-editor lease contract

Each video permits at most two active edit leases by default.

- lease TTL: 180 seconds by default;
- heartbeat is requested at half the TTL;
- TTL is bounded to 60–900 seconds;
- an abandoned tab automatically stops consuming capacity after expiry;
- the same reviewer reacquiring a lease refreshes that reviewer’s slot rather
  than creating a second slot;
- lease browser tokens are opaque; SQLite stores only SHA-256 digests;
- progress saves do not require a lease;
- subtitle suggestion mutations require a valid lease token in
  `X-Review-Lease`, in addition to the M1 session and CSRF checks.

Runtime limit:

```text
REVIEW_MAX_EDITORS_PER_VIDEO=2
```

## Reviewer API

Read routes:

- `GET /api/v1/review/videos`
- `GET /api/v1/review/videos/{youtube_video_id}`

Mutation routes:

- `POST /api/v1/review/videos/{youtube_video_id}/lease`
- `POST /api/v1/review/videos/{youtube_video_id}/lease/heartbeat`
- `POST /api/v1/review/videos/{youtube_video_id}/lease/release`
- `POST /api/v1/review/videos/{youtube_video_id}/progress`
- `POST /api/v1/review/videos/{youtube_video_id}/segments/{segment_id}/suggestion`

All routes require the M1 reviewer session. Mutations also require the M1
same-origin + `X-Review-CSRF` guard.

## Video library response

The library gives the frontend enough state to prioritize resume behavior:

- video title and duration;
- fixed subtitle segment count;
- last playback position;
- reviewed-through position;
- completed flag;
- active edit-seat count;
- current reviewer suggestion count;
- most recently updated resume point.

## Synchronized subtitle workspace

The browser loads the YouTube IFrame API and uses the stored YouTube video ID.
It does not proxy or download video media.

The player page:

- resumes the saved playback position;
- samples current player time approximately every 300 ms;
- highlights the subtitle whose fixed `[start_ms, end_ms)` contains that time;
- scrolls the active subtitle into view;
- lets the reviewer click a subtitle timestamp to seek the YouTube player;
- saves playback progress every 10 seconds;
- displays current active reviewers;
- enters read/write mode only after an edit lease is acquired.

The fixed subtitle timing remains unchanged when a reviewer suggests text edits.

## Suggestion semantics

If a reviewer has no pending suggestion for a segment, the first save creates a
new suggestion and contribution credit begins immediately.

If the same reviewer edits that pending suggestion again, M2 revises the same
record rather than creating a second contribution item. This preserves the M0
contribution accounting contract.

M2 still does **not** change `working_text` directly. Owner approval remains a
later milestone.

## Automated coverage

`tests/test_review_portal.py` covers:

- at most two active reviewers per video;
- automatic capacity recovery after lease expiry;
- capacity recovery after explicit release;
- video list and fixed subtitle detail reads;
- progress saving without consuming a seat;
- suggestion rejection without an edit lease;
- create-then-revise behavior for one pending suggestion;
- immediate contribution totals remaining one suggestion after revision;
- third reviewer receiving HTTP 409 until capacity becomes available.

The existing M0/M1 tests remain part of the full repository suite.

## Not yet implemented in this M2 slice

The database/API/UI can already render any imported `review_videos` and
`review_subtitle_segments`, but the production YouTube playlist/caption importer
is still a separate step.

That importer will need a channel-owner YouTube OAuth credential distinct from
reviewer Google/LINE login. It must import the authorized playlist/video/caption
metadata into the M0 tables without granting reviewers channel-management
permissions.

Versioned owner approval, batch replacement, contribution-board UI, and YouTube
caption publish/rollback also remain later milestones.
