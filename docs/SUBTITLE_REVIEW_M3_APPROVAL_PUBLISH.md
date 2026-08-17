# Subtitle Review M3 — approval, versioning, batch replace, and YouTube publish

M3 closes the review loop after M0–M2:

```text
Reviewer suggestion
  -> owner review
  -> conflict check
  -> approve / reject
  -> immutable subtitle version
  -> optional batch correction
  -> explicit YouTube publish
  -> historical version can be republished as online rollback
```

The owner/admin surface is deliberately separate from reviewer authentication.
Reviewer Google/LINE sessions never authorize owner review or YouTube channel
changes.

## Security boundary

All M3 owner routes use the existing Cloudflare Access operator identity:

```text
/api/v1/review-admin/*
```

Production edge policy must keep this path behind the admin Cloudflare Access
application. Do not expose it through the public reviewer policy used for:

```text
/review
/api/v1/review/*
```

M3 mutations continue to use the existing same-origin enforcement in
`app.api._mutation_actor` when `COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS=true`.

## Suggestion approval contract

A reviewer suggestion contains both:

- `base_segment_revision`
- `original_text_snapshot`

Before approval, M3 compares both values with the current segment.

Approval is allowed only when:

```text
suggestion.base_segment_revision == segment.revision
AND
suggestion.original_text_snapshot == segment.working_text
```

If either differs, the suggestion is a conflict. It remains pending and the
owner UI disables direct approval. This prevents a stale suggestion from
silently overwriting a correction that was accepted later.

Successful approval:

1. changes only `working_text`;
2. increments the segment revision;
3. marks the suggestion approved;
4. writes an owner audit event;
5. freezes a complete subtitle version for that video.

Rejection never changes `working_text`; the owner actor and optional reason are
preserved in `review_admin_audit`.

## Immutable subtitle versions

Every frozen version stores:

- video ID;
- monotonically increasing `version_number`;
- parent version ID;
- source (`suggestion_approval`, `batch_replacement`, `restore_version`, ...);
- source reference;
- complete fixed-segment JSON snapshot;
- rendered SRT;
- SHA-256 of that SRT;
- owner actor and creation timestamp;
- publish state and YouTube response/error evidence.

The fixed subtitle timing is preserved. Approval and batch replacement change
text only.

Historical versions are never rewritten.

## Restore vs. republish

These are intentionally different operations.

### Restore a historical version locally

```text
POST /api/v1/review-admin/versions/{version_id}/restore
```

Restore copies the historical texts back into the current working segments only
when the segment count, index, start time, and end time still match. It then
creates a **new** version recording the restore.

It does not call YouTube.

### Republish a historical version to YouTube

```text
POST /api/v1/review-admin/versions/{version_id}/publish
```

Publishing an older immutable version is the online rollback primitive. It
updates YouTube with the exact SRT frozen in that historical version.

Republishing an older version does not automatically rewrite the local working
copy. If the owner wants both local state and YouTube to match that historical
version, restore locally first and then publish the newly created restore
version.

## Batch replacement contract

Batch correction is deliberately two-phase.

### 1. Create preview

```text
POST /api/v1/review-admin/batches
```

Input contains literal `find_text`, `replace_text`, and optionally selected video
IDs. M3 records one occurrence item per matched segment with:

- segment ID;
- video ID;
- base revision;
- original text snapshot;
- proposed replacement text.

The preview is persistent so the owner can inspect individual occurrences.

### 2. Apply selected occurrences

```text
POST /api/v1/review-admin/batches/{batch_id}/apply
```

The owner may select all or only specific preview items. Each selected item is
revalidated against the current revision and text before mutation.

Outcomes:

- `applied`: still matches preview; replacement is applied;
- `conflict`: subtitle changed after preview; item is excluded;
- `skipped`: owner did not select the occurrence.

After text changes, M3 creates one immutable subtitle version per affected
video, not one version per occurrence.

## YouTube caption publishing

M3 reuses the owner-only OAuth credential introduced in M2. Reviewer OAuth
credentials are never used.

Required server configuration remains:

```text
YOUTUBE_OWNER_CLIENT_ID=<google oauth client id>
YOUTUBE_OWNER_CLIENT_SECRET=<google oauth client secret>
YOUTUBE_OWNER_REFRESH_TOKEN=<protected refresh token>
# or
YOUTUBE_OWNER_REFRESH_TOKEN_FILE=/run/secrets/youtube-owner-refresh-token
```

The channel-owner OAuth consent must include:

```text
https://www.googleapis.com/auth/youtube.force-ssl
```

YouTube Data API caption management does not support service-account channel
ownership. The refresh token must therefore belong to a Google account that can
edit the target YouTube videos/caption tracks.

### Publish request

M3 publishes the exact immutable SRT by version ID to the caption track ID saved
at M2 import time.

The request uses the YouTube Data API `captions.update` upload endpoint with:

```text
PUT /upload/youtube/v3/captions?part=id&uploadType=multipart
Content-Type: multipart/related
```

The metadata part contains only the existing caption track ID and the media part
contains the frozen SRT bytes. M3 never uses the deprecated caption `sync`
parameter; timestamps come from the application's fixed subtitle segments.

The implementation rejects a caption payload larger than 100 MB before network
I/O and validates that YouTube returns the expected caption track ID.

### Publish confirmation and idempotency

Publishing is quota-consuming and therefore requires an explicit
`{"confirm": true}` request.

If the exact version is already marked `published` for the same caption track,
M3 returns `already_published=true` and does not call YouTube again.

On success:

- the target version becomes `published`;
- the previously published version for that video becomes `superseded`;
- YouTube response evidence is stored.

On failure:

- the version becomes `publish_failed`;
- the error is persisted;
- the API returns an error and never claims the version was published.

## Quota strategy

A YouTube `captions.update` call currently costs 450 quota units. Do not publish
once per reviewer suggestion.

Recommended flow:

```text
many reviewer suggestions
  -> owner approves / batches locally
  -> one final version per affected video
  -> publish once per affected video
```

This preserves quota and also gives the owner a meaningful version boundary.

## Owner UI

`/review-admin` provides three responsive tabs:

1. **待審建議** — reviewer, time, video position, original/suggested text,
   conflict state, approve/reject;
2. **批次修正** — literal find/replace preview, per-occurrence selection,
   conflict exclusion, explicit apply;
3. **版本與發布** — version history, SHA prefix, local restore, YouTube publish,
   and historical republish warning.

The same UI adapts to desktop, tablet, and mobile. It is an owner/admin page and
must remain behind Cloudflare Access.

## Automated coverage

M3 adds:

- `tests/test_review_admin_store.py`
  - approval + version freeze;
  - stale suggestion conflict;
  - rejection audit;
  - selected batch apply;
  - batch revision recheck;
  - historical restore while preserving timing;
- `tests/test_review_admin_api.py`
  - explicit confirmations;
  - compact version lists vs full version detail;
  - owner review actor evidence;
  - publish idempotency;
  - persisted publish failure;
- `tests/test_review_youtube_publish.py`
  - multipart metadata/media body;
  - PUT upload endpoint and query contract;
  - upload-size fail-closed guard;
  - unexpected caption-track response rejection.

Repository CI never performs a real YouTube request.

## Production smoke checklist — requires explicit deployment authorization

Do not perform these steps from CI.

1. configure reviewer and admin Cloudflare edge policies separately;
2. configure protected Google/LINE reviewer OAuth credentials from M1;
3. configure the channel-owner YouTube OAuth client + refresh token;
4. configure/import the production playlist with M2 preview first;
5. verify one low-risk video appears in `/review/videos`;
6. submit one reviewer suggestion;
7. approve it in `/review-admin` and inspect the immutable version/SRT;
8. explicitly publish that version;
9. re-read or re-download the YouTube caption track and compare content;
10. test one historical republish/rollback on a controlled test video before
    enabling normal production use.

Deployment, live YouTube writes, external permission changes, and other real
provider operations remain explicit owner-authorized actions.
