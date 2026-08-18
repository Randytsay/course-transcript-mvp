# Subtitle Review UX V2 — reviewer/admin completeness

This increment turns the M0–M3 reviewer stack from a technically complete
controlled-review workflow into a clearer nontechnical共修 product surface.
It is intentionally based on the approved production boundary
`c86ed0f8ad1347479b9d47961aaee2725d3ace97` and does **not** authorize a live
deployment, playlist import, or YouTube write.

## Product principles

The reviewer experience is designed for people who should not need to understand
concurrency leases, API concepts, caption tracks, or subtitle-editor internals.
The visible flow is deliberately small:

```text
choose a video
  -> watch and review
  -> notice a subtitle error
  -> start editing
  -> edit one line
  -> submit a suggestion
  -> see whether it is pending / accepted / not accepted
```

Engineering concepts remain internal:

- edit lease;
- TTL / heartbeat;
- maximum concurrent editor enforcement;
- revision/conflict tokens;
- caption-track IDs;
- OAuth refresh tokens.

The UI may explain that only a small number of people can modify one video at the
same time when capacity is reached, but it does not expose the term `席位` or ask
the reviewer to understand lease mechanics.

## Reviewer landing page

`/review` now explains the workflow before login:

1. choose a video;
2. review while watching;
3. submit a correction suggestion.

It explicitly states:

- reviewers do not edit timestamps;
- a suggestion does not immediately modify YouTube;
- the owner/admin reviews suggestions before they become official subtitle text;
- progress can be resumed across devices after login.

## Watched progress vs reviewed progress

Watching and reviewing are different activities.

The video library and workspace now distinguish:

- **觀看進度**: last playback position;
- **校閱進度**: the furthest position the reviewer explicitly says they have
  checked;
- **完成校閱**: an explicit reviewer action, never inferred merely because the
  player reached the end.

The existing persistence fields are used:

```text
last_playback_ms
reviewed_until_ms
last_segment_index
completed
```

The workspace provides:

- `我已校閱到這裡`;
- `完成本片校閱`.

Completion is monotonic in the existing store. A completed reviewer may still
watch the video and submit later suggestions.

## Video library

`/review/videos` now supports larger playlists with:

- title/keyword search;
- filters for not started, reviewing, completed, and videos the current reviewer
  has participated in;
- title or review-progress sorting;
- separate watched/reviewed progress bars;
- current editing activity in plain language;
- the current reviewer's suggestion and pending counts.

Reviewer empty states do not mention playlist synchronization or API concepts.

## Subtitle workspace

`/review/videos/{videoId}` retains the original fixed-timing YouTube-synchronized
editor and adds:

- plain-language edit-state messages instead of seat/lease wording;
- explicit review progress controls;
- follow-playback ON/OFF;
- automatic pause of auto-follow while one line is being edited;
- browser-local draft persistence per video;
- `beforeunload` protection while unsent drafts exist;
- suggestion status badges: pending, accepted, not accepted, withdrawn;
- withdrawal of a still-pending suggestion;
- canonical current subtitle shown above the edit field;
- draft preservation when the reviewer closes the edit field without submitting.

The timing fields remain immutable throughout suggestion submission.

## Suggestion withdrawal

The existing database status constraint permits only:

```text
pending | approved | rejected
```

V2 therefore keeps the schema backward compatible: a reviewer withdrawal marks
the pending suggestion `rejected` and appends a
`review_suggestion_events.event_type = withdrawn` event by that same reviewer.
Reviewer APIs project this state as `withdrawn`.

Withdrawn suggestions:

- remain in history for auditability;
- are not shown as pending;
- cannot be approved later;
- are excluded from contribution counts.

This avoids destructive deletion and avoids a production schema migration solely
for the UI state.

## Reviewer feedback loop

`/api/v1/review/suggestions/me` exposes the current reviewer's suggestion
history with video/segment/time information and a display status.

`/review/contributions` is reframed as **我的共修紀錄** and shows:

- completed reviews;
- participating videos;
- active suggestion count;
- accepted suggestions;
- pending suggestions;
- changed-character count;
- per-suggestion history and any owner rejection reason.

The shared board is de-emphasized as competition. Ranking prioritizes completed
reviews and videos participated in before raw suggestion volume. Withdrawn
suggestions do not add credit.

## Admin: YouTube playlist onboarding

`/review-admin` now has a dedicated **影片同步** stage.

The owner first runs a preview:

```json
{
  "apply": false,
  "max_videos": 10
}
```

The UI displays each video's status, including:

- ready;
- already in the review system;
- no matching caption;
- failed.

Only `ready` rows can be selected. The import request is then restricted to the
explicitly selected YouTube video IDs:

```json
{
  "apply": true,
  "max_videos": 2,
  "youtube_video_ids": ["...", "..."]
}
```

The importer scans the configured playlist for those IDs, preserves requested
order, and continues to use the existing strict SRT parser and non-overwrite
contract.

Import downloads selected YouTube caption text into the local reviewer database.
It does **not** modify YouTube.

## Admin: suggestion review context

Pending suggestions now support an owner-only read path for:

- previous subtitle;
- current subtitle;
- next subtitle;
- video/time location.

The admin UI adds:

- compact inline character diff;
- original vs proposed side-by-side text;
- expandable previous/current/next context;
- embedded YouTube playback starting shortly before the suggested location;
- the existing revision conflict warning and fail-closed approval behavior.

Approval copy explicitly tells the owner that approving creates a local version
and does not publish to YouTube.

## Admin: versioning separated from publication

The previous combined `版本與發布` surface is split into:

- **版本管理** — history, SHA, source, local restore;
- **YouTube 發布** — explicit online publication only.

This makes the local/remote state distinction visible:

```text
local working version != YouTube current version
```

Historical restore remains a local operation and creates a new immutable
version, preserving the existing M3 contract.

## YouTube publish preflight

Before a publish button can become actionable, the owner UI calls the read-only:

```text
GET /api/v1/review-admin/versions/{version_id}/publish-preview
```

The response reports:

- target version / SHA;
- whether it is the latest local version;
- reference version;
- changed subtitle segment count;
- changed-character count;
- fixed timing policy;
- whether a caption track is configured.

The UI requires an explicit checkbox and then a second confirmation before the
existing publish mutation is invoked.

Publishing a historical version is visually identified as an online rollback.
No code in this PR automatically invokes publication.

## Admin audit surface

`GET /api/v1/review-admin/audit` provides a compact owner-only projection of the
existing `review_admin_audit` table. The UI renders important actions such as:

- suggestion approve/reject;
- batch preview/apply;
- version create/restore;
- YouTube publish success/failure.

The audit endpoint does not return full immutable version snapshots or SRT text.

## Security and isolation preserved

This increment does not change the core security boundaries:

- reviewer Google/LINE sessions authorize only `/api/v1/review/*`;
- reviewer mutations still require same-origin + reviewer CSRF;
- subtitle suggestion mutation still additionally requires a valid internal edit
  lease;
- `/api/v1/review-admin/*` remains Cloudflare-Access/operator protected;
- YouTube channel-owner OAuth remains separate from reviewer login;
- fixed subtitle timings remain unchanged by review text edits;
- the existing owner approval conflict check remains authoritative;
- publication still requires explicit owner confirmation.

## Automated coverage added/updated

Python coverage includes:

- watched/reviewed/completed progress projection;
- reviewer suggestion history and withdrawal lifecycle;
- contribution totals excluding withdrawn suggestions;
- selected-video playlist scanning/import preview;
- owner suggestion context;
- admin audit projection;
- read-only publish preflight and fixed-timing reporting.

Frontend source-contract tests lock the important product boundaries:

- three-step nontechnical onboarding;
- no visible `席位` wording in reviewer pages;
- watched and reviewed progress remain distinct;
- admin import/version/publish stages remain separate;
- publish preflight remains explicit.

## Required AI-agent validation before merge/deploy

This branch must be handed to the local AI agent for execution-based validation.
The agent should use the branch exact SHA and perform, at minimum:

```text
1. git diff --check
2. Python compile/import checks
3. focused review tests
4. complete Python test suite
5. npm --prefix frontend test
6. npm --prefix frontend run build
7. dependency audit according to current repo CI
8. Docker application/frontend ARM64 build/revision checks
9. secret-prefix / credential scan
10. Playwright desktop + mobile reviewer/admin UX smoke against an isolated stack
```

The agent must not deploy this branch merely because tests pass.

## Production hold

This UX increment does not authorize:

- production deployment;
- a production `apply=true` import;
- bulk/full-playlist import;
- scheduled playlist synchronization;
- a real YouTube `captions.update`;
- republish/online rollback.

Those remain separately authorized production milestones using an exact reviewed
and approved Git SHA.
