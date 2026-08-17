# Subtitle Review Production Preparation

This document prepares the merged M0–M3 subtitle-review stack for production
without performing a live deployment or any real Google/LINE/YouTube mutation.

## Production hostnames

Two public hostnames intentionally point to the same Next.js frontend service:

```text
transcript.randy88.ccwu.cc -> Cloudflare Access -> frontend:3000
review.randy88.ccwu.cc     -> public edge       -> frontend:3000
```

Cloudflare Tunnel routing is already configured for both hostnames.

The security model is deliberately different:

- `transcript.randy88.ccwu.cc` remains behind Cloudflare Access and is the
  operator/admin hostname;
- `review.randy88.ccwu.cc` is reachable without Cloudflare Access because
  reviewers authenticate inside the application with Google or LINE Login.

## Public reviewer hostname isolation

Because both hostnames terminate at the same Next.js service, the reviewer
hostname must not expose the original transcript UI or arbitrary same-origin API
routes.

`frontend/proxy.ts` therefore applies a hostname boundary before normal Next.js
routing/rewrites.

On `review.randy88.ccwu.cc`:

```text
/                              -> 307 /review
/review                        -> allowed
/review/*                      -> allowed
/api/v1/review                 -> allowed
/api/v1/review/*               -> allowed
/api/* other than reviewer API -> 404
all other pages                -> 307 /review
```

Static Next.js assets remain available so reviewer pages can render normally.

This edge/application boundary is defense in depth; admin APIs still require the
existing Cloudflare Access identity at the FastAPI layer.

`transcript.randy88.ccwu.cc` and local-development hostnames retain existing
behavior.

## Compose overlay

Production review settings are isolated in:

```text
docker-compose.review.yml
```

Apply it after the normal base and immutable release compose files:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  -f docker-compose.review.yml \
  --profile web \
  config
```

The overlay does not contain real credentials.

## Non-secret production values

The current intended values are:

```text
REVIEW_PUBLIC_ORIGIN=https://review.randy88.ccwu.cc
REVIEW_PUBLIC_HOSTNAME=review.randy88.ccwu.cc
REVIEW_MAX_EDITORS_PER_VIDEO=2
YOUTUBE_REVIEW_PLAYLIST_ID=PL9bFsGt4TAeUxZhzEhoxmY16ek2I5cOhQ
YOUTUBE_REVIEW_CAPTION_LANGUAGES=zh-TW,zh-Hant,zh
```

The playlist is the canonical source of reviewer videos. Initial production
acceptance should still use preview mode and one controlled video before a
full-playlist import.

## Secrets that must be created before deployment

Do not commit these values and do not paste them into GitHub issues/PRs.

Protected production env file:

```text
REVIEW_GOOGLE_CLIENT_ID=...
REVIEW_GOOGLE_CLIENT_SECRET=...
REVIEW_LINE_CHANNEL_ID=...
REVIEW_LINE_CHANNEL_SECRET=...
YOUTUBE_OWNER_CLIENT_ID=...
YOUTUBE_OWNER_CLIENT_SECRET=...
YOUTUBE_OWNER_REFRESH_TOKEN_HOST_PATH=/opt/course-transcript/secrets/youtube-owner-refresh-token
```

The channel-owner refresh token itself belongs in:

```text
/opt/course-transcript/secrets/youtube-owner-refresh-token
```

Recommended permissions:

```bash
sudo install -d -m 700 /opt/course-transcript/secrets
sudo touch /opt/course-transcript/secrets/youtube-owner-refresh-token
sudo chmod 600 /opt/course-transcript/secrets/youtube-owner-refresh-token
```

The file may contain either the raw refresh token or a JSON object with a
`refresh_token` field. It is mounted read-only into the API container.

## Google reviewer Login

Create a Google Web OAuth client for reviewer authentication.

Authorized redirect URI:

```text
https://review.randy88.ccwu.cc/api/v1/review/auth/google/callback
```

The reviewer flow requests only OpenID identity scopes (`openid email profile`).
It is separate from YouTube channel-owner OAuth.

## LINE reviewer Login

Create/configure a LINE Login channel.

Callback URL:

```text
https://review.randy88.ccwu.cc/api/v1/review/auth/line/callback
```

The application requests `openid profile email`. Email availability depends on
the LINE channel/account configuration; account identity itself uses the LINE
subject, not email.

## YouTube channel-owner OAuth

This credential is server-only and must belong to a Google account that can edit
the target YouTube videos/caption tracks.

Required caption-management scope:

```text
https://www.googleapis.com/auth/youtube.force-ssl
```

Reviewer Google Login credentials must never be reused as YouTube owner
credentials.

## Pre-deployment checks

Before changing the live containers:

1. `transcript.randy88.ccwu.cc` still requires Cloudflare Access in a private
   browser session;
2. `review.randy88.ccwu.cc` does not require Cloudflare Access;
3. both Tunnel routes target `http://frontend:3000`;
4. production env file exists and is root/operator protected;
5. Google reviewer redirect URI is registered exactly;
6. LINE callback URL is registered exactly;
7. YouTube owner token file exists with restrictive permissions;
8. `docker compose ... config` with `docker-compose.review.yml` succeeds;
9. build/test CI for this deployment-prep PR is green.

## Deployment hold point

Do not perform a live deployment merely because this preparation PR is green.
Deployment should be a separate explicitly authorized step using an exact
approved Git SHA and the existing immutable release process.

The first live deployment should bring up the reviewer UI/auth boundary while
keeping YouTube write operations unused until smoke testing reaches the publish
step.

## First production smoke

Recommended order after deployment:

```text
1. GET https://review.randy88.ccwu.cc
   -> redirects to /review
2. attempt https://review.randy88.ccwu.cc/review-admin
   -> redirects to /review
3. attempt https://review.randy88.ccwu.cc/api/v1/review-admin/overview
   -> 404 at reviewer hostname boundary
4. Google reviewer login
5. LINE reviewer login
6. YouTube playlist sync with apply=false
7. choose one controlled video
8. import that one video/caption
9. reviewer watches and submits one correction
10. owner sees it in transcript.../review-admin
11. approve -> v1 baseline remains, v2 is created
12. inspect immutable SRT/diff
13. only then explicitly authorize one YouTube publish
14. re-download/re-read caption and verify
15. verify historical-version rollback on the controlled video
```

## Deferred until the first smoke passes

Do not enable these before the controlled end-to-end test succeeds:

- full-playlist import;
- scheduled hourly playlist synchronization;
- automatic new-video badges/notifications;
- broad reviewer announcement;
- normal YouTube publishing of production corrections.
