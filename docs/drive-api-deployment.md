# Google Drive API browser deployment

This release separates interactive browsing from bulk file transfer.

- Google Drive API: directory browsing, search, pagination, selection metadata and health checks.
- rclone: media download, result upload, backup, promotion/versioning and retries.

No Drive mutation is performed by the browser routes.

## Required Google Cloud setup

1. Enable **Google Drive API** in the Google Cloud project used for the OAuth client.
2. Use the same OAuth client that owns the existing `gdrive:` refresh token, or create a dedicated OAuth client with read access to the required Drive content.
3. Confirm the refresh token has not been revoked and the account can see the intended My Drive or Shared Drive folders.
4. Keep the refresh token and client secret outside Git and outside Docker images.

The API accepts either:

- explicit environment variables:
  - `GOOGLE_DRIVE_CLIENT_ID`
  - `GOOGLE_DRIVE_CLIENT_SECRET`
  - `GOOGLE_DRIVE_REFRESH_TOKEN` or `GOOGLE_DRIVE_REFRESH_TOKEN_FILE`
- or the existing mounted rclone config, from which it reads `client_id`, `client_secret`, `token.refresh_token` and optional `root_folder_id`.

The browser refreshes access tokens in process memory. It does not write updated tokens back to the secret file.

## Production environment

Recommended values:

```env
COURSE_TRANSCRIPT_DRIVE_BROWSER_PROVIDER=google_api
COURSE_TRANSCRIPT_DRIVE_BROWSER_FALLBACK=true
COURSE_TRANSCRIPT_DRIVE_REMOTE=gdrive
GOOGLE_DRIVE_PAGE_SIZE=200
RCLONE_CONFIG_HOST_PATH=/home/ubuntu/.config/rclone/rclone.conf
```

Optional dedicated refresh-token file:

```env
GOOGLE_DRIVE_CLIENT_ID=<oauth-client-id>
GOOGLE_DRIVE_CLIENT_SECRET=<oauth-client-secret>
GOOGLE_DRIVE_REFRESH_TOKEN_FILE=/run/secrets/google-drive-refresh-token
```

When using a dedicated token file, mount it read-only into the API container. Do not print it in deployment logs.

## Build and deployment gate

Before changing the live service:

1. Confirm `active`, `queued` and `processing` jobs are zero.
2. Confirm non-expired leases are zero.
3. Confirm pending delivery candidates are zero.
4. Preserve the current API image tag and live root for rollback.
5. Build the API image for `linux/arm64`.
6. Verify the image contains rclone `v1.74.0`.
7. Run Python tests, frontend production build and Compose validation.

Only these services require new images for this feature:

- `api`
- `frontend`

Workers may also be rebuilt to align their rclone binary with `v1.74.0`, but no worker command or transfer contract changes in this PR.

Cloudflared must not be restarted or recreated.

## Suggested deployment commands

Use the project and paths already approved for production. Do not use `docker compose down` or `--remove-orphans`.

```bash
cd /opt/course-transcript

docker compose -p course-transcript -f docker-compose.yml --profile web config --quiet

docker compose -p course-transcript -f docker-compose.yml --profile web build api frontend worker pipeline-worker delivery-worker

docker compose -p course-transcript -f docker-compose.yml --profile web \
  up -d --no-deps --force-recreate \
  api worker pipeline-worker delivery-worker frontend
```

After recreation, verify:

- API and frontend are healthy.
- Cloudflared container ID is unchanged.
- `/app/data` still points to `/opt/course-transcript/data`.
- `rclone version` in API and workers reports `v1.74.0`.
- OAuth and rclone secret files are not printed.

## Read-only smoke test

Run after deployment:

```bash
cd /opt/course-transcript
bash scripts/smoke_drive_api_readonly.sh
```

Expected properties:

- `/api/v1/drive/health` returns HTTP 200.
- provider is `google_api` when OAuth is valid.
- `/api/v1/drive/browse` returns HTTP 200 with a non-negative entry count.
- no file or folder names are printed by the script.
- no Drive mutation is made.
- no Chirp or Gemini call is made.

A healthy example looks like:

```text
DRIVE_API_READONLY_SMOKE
{"endpoint":"/api/v1/drive/health","http_status":200,"provider":"google_api",...}
{"endpoint":"/api/v1/drive/browse","http_status":200,"provider":"google_api","entry_count":20,...}
paid_provider_calls_made=NO
drive_mutations_made=NO
secrets_printed=NO
```

## Fallback interpretation

`provider=rclone_fallback` means the Google Drive API browser failed and the API served the first directory page through rclone. The UI displays this state explicitly.

Fallback is acceptable as a temporary availability measure, but the deployment gate should remain incomplete until `/api/v1/drive/health` reports `status=ok` and `provider=google_api`.

Search and pagination beyond the first rclone listing require the Google Drive API browser.

## Rollback

Rollback the API/frontend images and Compose root using the preserved production rollback procedure when:

- API or frontend does not become healthy;
- existing job/database state changes unexpectedly;
- the browser causes repeated 5xx responses;
- the deployed image or command does not match the approved build.

Do not rollback solely because Drive health reports an OAuth configuration error while all existing services remain healthy. In that case, keep the service running, disable the provider or use the approved rclone fallback while correcting OAuth configuration.
