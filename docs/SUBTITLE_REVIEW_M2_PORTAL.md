# Subtitle Review M2 — reviewer video workspace

M2 turns the M0/M1 identity and persistence foundation into an actual reviewer
workspace and adds a separate owner-only YouTube playlist/caption import path.
It remains stacked on M1; caption publishing back to YouTube is still deferred.

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

## Owner-only YouTube import

Reviewer Google/LINE login is **not** used for YouTube channel access.

The importer is mounted separately at:

```text
POST /api/v1/review-admin/youtube/sync
```

It calls the existing operator `_mutation_actor` boundary, so production should
keep `/api/v1/review-admin/*` behind the Cloudflare Access admin policy while the
reviewer-facing `/review` and `/api/v1/review/*` paths use M1 app authentication.

The importer uses a separate Google OAuth client + refresh token belonging to the
YouTube channel owner.

Configuration:

```text
YOUTUBE_REVIEW_PLAYLIST_ID=<playlist id>
YOUTUBE_OWNER_CLIENT_ID=<google oauth client id>
YOUTUBE_OWNER_CLIENT_SECRET=<google oauth client secret>
YOUTUBE_OWNER_REFRESH_TOKEN=<protected refresh token>
# or use a mounted file instead:
YOUTUBE_OWNER_REFRESH_TOKEN_FILE=/run/secrets/youtube-owner-refresh-token
YOUTUBE_REVIEW_CAPTION_LANGUAGES=zh-TW,zh-Hant,zh
```

Do not commit those credential values to the repository.

### Preview vs apply

Request example:

```json
{
  "apply": false,
  "max_videos": 50
}
```

Preview enumerates playlist videos and selects a matching non-draft caption track
without downloading or mutating subtitle rows.

To perform the initial import:

```json
{
  "apply": true,
  "max_videos": 50
}
```

For each new video, the importer:

1. reads playlist metadata;
2. skips immediately if review segments already exist;
3. lists caption tracks using channel-owner OAuth;
4. prefers configured languages and standard/manual tracks;
5. downloads the selected track as SRT;
6. parses the entire SRT with the existing strict non-overlapping parser;
7. inserts video metadata + fixed segments only after parsing succeeds.

An invalid SRT fails that video without creating partial review rows.

Initial sync is intentionally non-destructive. Existing subtitle segments are
never overwritten; a future refresh of an already-reviewed YouTube caption must
use a versioned reconciliation flow.

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

`tests/test_review_youtube_import.py` covers:

- preview without caption download/database mutation;
- successful strict SRT import;
- existing-video short circuit before caption-list/download calls;
- configured-language/manual-track selection;
- malformed SRT rejection without partial rows.

The existing M0/M1 tests remain part of the full repository suite.

## Still deferred

Real provider smoke requires a channel-owner refresh token authorized for the
YouTube caption scope and the production playlist ID. Repository CI uses mocks
and never calls YouTube.

Versioned owner approval, batch replacement, contribution-board UI, and YouTube
caption publish/rollback remain later milestones.
