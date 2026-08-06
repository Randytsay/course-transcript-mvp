# Immutable VPS release layout

This layout separates immutable application source from persistent production state.
It is intended for the current Oracle ARM64 host, where the running Compose project is
`course-transcript-source` and the persistent state currently lives below
`/opt/course-transcript-source`.

## Directories

- Release source: `/opt/course-transcript-releases/<exact-git-sha>`
- Persistent data: `/opt/course-transcript-source/data`
- Persistent logs: `/opt/course-transcript-source/logs`
- Persistent tmp: `/opt/course-transcript-source/tmp`
- GCP service account: `/opt/course-transcript/secrets/gcp-sa.json`
- rclone config: `/home/ubuntu/.config/rclone/rclone.conf`
- Protected service environment file: `/home/ubuntu/.env`
- Compose project: `course-transcript-source`

The release directory must be created from an exact approved commit with `git archive`.
Do not checkout, reset, clean, or otherwise modify the dirty live source worktree merely
to deploy a release.

## Required environment

Before rendering or building the release Compose model, export:

```bash
export COURSE_TRANSCRIPT_RELEASE_TAG='<exact-approved-git-sha>'
export COURSE_TRANSCRIPT_ENV_FILE='/home/ubuntu/.env'
export COURSE_TRANSCRIPT_TARGETARCH='arm64'
export RCLONE_RELEASE='1.74.0'
export COURSE_TRANSCRIPT_DATA_HOST_PATH='/opt/course-transcript-source/data'
export COURSE_TRANSCRIPT_LOGS_HOST_PATH='/opt/course-transcript-source/logs'
export COURSE_TRANSCRIPT_TMP_HOST_PATH='/opt/course-transcript-source/tmp'
export GCP_CREDENTIALS_HOST_PATH='/opt/course-transcript/secrets/gcp-sa.json'
export RCLONE_CONFIG_HOST_PATH='/home/ubuntu/.config/rclone/rclone.conf'
export GOOGLE_DRIVE_REFRESH_TOKEN_HOST_PATH='/dev/null'
export BILLING_CREDENTIALS_HOST_PATH='/opt/course-transcript/secrets/billing-sa.json'
```

Use the base Compose file together with `docker-compose.release.yml`:

```bash
sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  config
```

`--env-file` supplies Compose interpolation values. `COURSE_TRANSCRIPT_ENV_FILE` separately
ensures services that use Compose `env_file` load the protected production file rather
than looking for a relative `.env` inside the immutable release directory.

The release override gives every built image an exact-SHA tag, passes the same SHA as the
`VCS_REF` build argument, fixes the target architecture and rclone release, and replaces
every `/app/data`, `/app/logs`, and `/app/tmp` bind source with an explicit persistent
host path. Both backend and frontend images must expose the approved SHA through the OCI
label `org.opencontainers.image.revision`. This prevents a release checked out elsewhere
from accidentally starting with an empty database or producing an image whose internal
provenance does not match its tag.

## Mandatory render and image checks

Before any restart, verify the rendered model and built images:

1. `api`, `pipeline-worker`, `delivery-worker`, `worker`, and `billing-worker` use the
   same `/app/data` source.
2. No `/app/data` mount points into the release source directory.
3. GCP, rclone, refresh-token, and billing credential mounts are read-only.
4. The API does not mount the GCP service account.
5. `infrastructure-test`, `api`, and `billing-worker` include the protected absolute
   service env file; no service relies on a release-local `.env`.
6. Every application image tag ends with the exact approved Git SHA.
7. Every backend build receives `TARGETARCH=arm64`, the approved rclone release, and
   `VCS_REF=<exact-approved-git-sha>`.
8. The frontend build receives `VCS_REF=<exact-approved-git-sha>`.
9. Every built backend and frontend image reports the exact approved SHA in
   `org.opencontainers.image.revision`; `unknown`, an empty value, or a different SHA is
   a deployment blocker.
10. Backend images report ARM64 architecture and the approved rclone version.
11. The frontend host binding remains `127.0.0.1:3300:3000`.
12. Cloudflared is not rebuilt and continues to route only to `frontend:3000`.

## Pending Drive delivery

A `pending_retry` Drive delivery is not an active Chirp or Gemini job and does not by
itself authorize a Drive mutation. Before deployment, record its job ID, delivery state,
transaction state, attempt count, and due time. Run the delivery-worker candidate
selection logic read-only. Do not call `run_once()` and do not manually retry.

If a due state is not selected as a candidate, stop and explain which candidate condition
is false. Do not clear or rewrite the state merely to make deployment proceed. A stale
`drive-delivery-state.json` whose publish transaction is already completed should remain
as audit evidence; operational reporting must distinguish this from an actionable retry
candidate.

During a controlled release, avoid recreating or restarting `delivery-worker` until the
pending state has been explained and an explicit resume policy has been approved. The
other non-paid services may be built and validated independently.

## Rollback

Keep the prior containers and image IDs recorded before replacement. Exact-SHA image tags
must not be overwritten. Persistent data is outside the release directory and must never
be deleted during source rollback. Preserve all provider manifests and Drive transaction
state.
