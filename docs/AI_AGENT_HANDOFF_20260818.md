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
with exact-SHA ARM64 images. Both services are healthy and use the approved
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

## YouTube reviewer state

The configured reviewer playlist ID is present in the production environment,
but the live `review_videos` table currently contains zero rows. Therefore no
playlist import has run yet.

The import route is owner-only:

```text
POST /api/v1/review-admin/youtube/sync
```

It is not authorized by reviewer Google/LINE login. It requires a separate
YouTube channel-owner OAuth client and refresh token, and is intended to run
behind the Cloudflare Access-protected admin origin. Current production is
deliberately fail-closed for this capability:

- owner OAuth client ID/secret are not configured;
- the owner refresh-token mount is `/dev/null`;
- no YouTube playlist/caption read or write has been performed.

The next operator must use the Google account that owns/manages the YouTube
videos and playlist. Configure that separate OAuth flow first, then use a
preview/read-only import before any apply/import mutation. Do not publish or
update YouTube captions as part of playlist onboarding.

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
live production facts in this document and the immutable `main` SHA above are
the current deployment boundary.
