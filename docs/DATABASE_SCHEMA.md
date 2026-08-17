# Database schema

SQLite WAL is stored under the persistent `data` volume.

- `batch_previews` / `source_previews`: expiring, immutable Drive selections.
- `batches`: aggregate item/duration/cost/status/revision.
- `jobs`: source metadata, state, progress, estimate/reservation/usage,
  approval, revision, global lease, and heartbeat.
- `job_events`: append-only actor/payload audit history.
- `stage_runs`: one durable row per stage with attempt count/checksum/times.
- `usage_records`: provider/model units and estimated cost, idempotent by
  `(job_id, dedupe_key)`.

Raw provider payloads and large derived artifacts remain files indexed by job;
the database stores state and audit metadata, not private transcript bodies.

## Subtitle review collaboration layer

The Buddhist YouTube subtitle-review feature shares the same SQLite/WAL file but
uses only `review_*` tables so it does not change the transcription job contract.

- `review_users`: logical reviewers, roles, status, and login timestamps.
- `review_auth_identities`: Google/LINE identities explicitly linked to a logical reviewer.
- `review_videos`: YouTube video/playlist/caption-track metadata.
- `review_subtitle_segments`: fixed imported cue timing plus original/working text.
- `review_suggestions`: reviewer correction proposals and changed-character count.
- `review_suggestion_events`: append-only submission/revision history.
- `review_video_progress`: cross-device playback/review continuation state.

Contribution statistics are derived from suggestions and progress rather than
stored as mutable counters. A submitted suggestion counts immediately, regardless
of its later owner-review status. See `docs/SUBTITLE_REVIEW_M0.md` for the complete
M0 contract and later authentication/publishing milestones.
