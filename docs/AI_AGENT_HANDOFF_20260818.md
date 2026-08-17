# AI Agent Handoff — Course Transcript MVP

Snapshot date: 2026-08-18 (Asia/Taipei)

## Production truth

- Repository: `Randytsay/course-transcript-mvp`
- Production host: `ubuntu@161.33.193.39`
- Immutable production source: `/opt/course-transcript-releases/<git-sha>`
- Persistent production data: `/opt/course-transcript-source/data`
- Production database: `/opt/course-transcript-source/data/course-transcript.db`
- Compose project: `course-transcript-source`
- Approved and deployed `main` SHA for the reviewer portal: `c86ed0f8ad1347479b9d47961aaee2725d3ace97`
- Production environment file: `/home/ubuntu/.env` (never print or copy it)

The current local handoff branch was created from the pre-existing local
`agent/m3-routing-ux` branch at `27a8c7e`. This branch is a handoff snapshot,
not a production deployment candidate. Do not deploy it in place of the
approved `main` SHA without a separate review and approval.

## Reviewer portal deployment

The 2026-08-18 reviewer auth-only cutover replaced only `api` and `frontend`
with exact-SHA ARM64 images. Both services were healthy and use the approved
SHA. The frontend remains loopback-only at `127.0.0.1:3300`.

The following services were checked before and after cutover and their
container IDs did not change:

- `worker`
- `pipeline-worker`
- `delivery-worker`
- `health-monitor`
- `retention-monitor`
- `cloudflared`

The cutover backup was verified at:
`/opt/course-transcript-backups/20260817T163650Z-reviewer-auth-cutover`

Non-paid evidence passed:

- full-release `tests.test_model_configuration`: 2 tests passed;
- `from app.api_hardened import app`: passed;
- API container health: HTTP 200;
- frontend root and `/api/v1/health` proxy: HTTP 200;
- reviewer `/` and `/review-admin`: 307 to `/review`;
- reviewer `/api/v1/review-admin/overview`: 404;
- reviewer auth providers: Google and LINE configured.

No Chirp/Gemini provider call, Drive mutation, YouTube caption publish, or
worker restart was part of this cutover.

## YouTube owner OAuth and read-only acceptance

Post-snapshot production acceptance was completed by the operator on
2026-08-18. Treat the following as the latest operator-confirmed production
state unless fresh VPS evidence contradicts it:

- YouTube owner OAuth client ID/secret are configured in protected production env;
- refresh token is stored outside the repo at
  `/opt/course-transcript/secrets/youtube-owner-refresh-token`;
- token file permissions were confirmed `600 root:root`;
- the API container sees the owner OAuth variables and the read-only token mount;
- refresh-token exchange succeeded;
- authorized YouTube channel identity was confirmed as:
  - title: `耀文Randy`
  - channel ID: `UCrGCs1F2hj3uIF8f5Yzbyfg`;
- reviewer playlist preview ran with `apply=false`, `max_videos=1`;
- preview result was `ready` for:
  - video ID: `Y365547A-MA`
  - title: `20260308 佛說彌勒大成佛經-1 | 八相成道`
  - caption language: `zh-Hant`;
- `review_videos` remained `0` before and after preview;
- no SRT download/import, full-playlist sync, caption update, or other YouTube
  write occurred;
- `worker`, `pipeline-worker`, `delivery-worker`, `frontend`, `health-monitor`,
  `retention-monitor`, and `cloudflared` container IDs remained unchanged during
  the API credential refresh/recreate work.

The import route is owner-only:

```text
POST /api/v1/review-admin/youtube/sync
```

It is not authorized by reviewer Google/LINE login. Owner/admin operations must
remain behind the Cloudflare Access-protected admin origin.

## Current reviewer smoke-test gate

The next controlled milestone is Phase 2. Do not skip directly to YouTube
publishing.

Required sequence:

1. Reconfirm live SHA and service health; do not build, pull, or recreate services.
2. Make a verified SQLite backup using SQLite backup semantics, not a raw WAL-unsafe copy.
3. Repeat `apply=false`, `max_videos=1`; require the same video `Y365547A-MA`,
   `zh-Hant`, `ready`.
4. Run the single allowed `apply=true`, `max_videos=1` import for that same item.
5. Verify exactly one `review_videos` row, valid caption metadata, non-empty
   fixed subtitle segments, and no unrelated reviewer rows.
6. Perform real reviewer login and workspace smoke; acquire one edit lease.
7. Submit exactly one minimal, safe, reversible suggestion; do not alter timing.
8. In the Cloudflare Access-protected owner UI, verify the suggestion and approve
   only that suggestion.
9. Verify imported-original baseline preservation and creation of a new immutable
   version, including JSON/SRT/SHA/diff and unchanged timing.
10. Use read-only YouTube checks to confirm the remote caption track still exists
    and was not modified by local approval.
11. Reconfirm all non-target container IDs are unchanged and API health is 200.
12. Stop. `captions.update`, publish, republish, rollback-publish, full-playlist
    import, and scheduled sync require a separate explicit owner authorization.

If any mutation request times out or loses its response, inspect DB state before
retrying. Never blindly re-run `apply=true` or approval after an ambiguous
failure.

## Safe continuation rules

1. Read `AGENTS.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, `HANDOVER.md`,
   `DEPLOYMENT.md`, and `docs/VPS_DEPLOY_GATE.md` before implementation.
2. Keep raw provider evidence, operation names, manifests, subtitle timing,
   database state, and Drive transaction state intact.
3. Never commit `/home/ubuntu/.env`, OAuth tokens, refresh tokens, service
   account JSON, rclone configuration, Cloudflare tunnel credentials, or
   database/backup contents.
4. Use immutable exact-SHA releases. Do not modify the dirty live source
   worktree, use `git reset --hard`, or use `git clean` on production.
5. Before any production restart, verify active paid jobs and leases, make a
   verified database/jobs backup, render the exact Compose layers, and record
   service container IDs.
6. A health endpoint alone is not acceptance; verify the real client path,
   protected service identity, database state, and downstream artifact.
7. The YouTube reviewer smoke must remain isolated from the paid transcription
   pipeline and from unrelated MiniMax/M3 experimentation.

## Handoff contents

This branch includes the current safe local code, validation scripts, reports,
and the frontend/M3 work that was present in the local worktree at snapshot
time. The following local artifacts were intentionally excluded from GitHub:

- `.playwright-cli/` browser logs and page snapshots;
- the `deploy/vertex-openai-proxy/*.bak-*` configuration backup;
- `scripts/.tmp_*` temporary scripts;
- derived lesson preview SRT/TXT/JSON files and other course-content outputs;
- runtime secrets, databases, backups, and provider credentials.

Historical reports may describe earlier gates or older production SHAs. The
approved production boundary remains the immutable `main` SHA above; the
post-snapshot OAuth/read-only state in this handoff supersedes the earlier
pre-OAuth snapshot notes for reviewer onboarding.
