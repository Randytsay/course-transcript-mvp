# Deployment

Production root: `/opt/course-transcript` on Oracle Ubuntu 24.04 ARM64.

The production deployment is gated. GitHub CI success does not authorize a VPS restart, paid provider request, or Drive mutation.

## Required target

- Branch: `main`
- Approved PR #8 merge commit: `e5acbf73aff14e55b790bd65a19cb822a1dbb17e`
- A later follow-up documentation/type-fix merge may supersede this SHA. Deploy only the exact `main` SHA explicitly approved for the VPS gate.
- API entry point: `app.api_hardened:app`
- pipeline entry point: `app.pipeline.dynamic_worker_production`
- delivery entry point: `app.jobs.delivery_worker`

## Pre-deployment gate

1. Confirm there is no active paid job, no new provider submission in progress, and no non-expired pipeline lease.
2. Record the current deployed Git SHA and rendered Compose service commands.
3. Create a timestamped backup directory outside the repository working tree.
4. Back up both:
   - `/opt/course-transcript/data/course-transcript.db`
   - `/opt/course-transcript/data/jobs/`
5. Verify the backup files exist, are non-empty where applicable, and can be listed without exposing secret contents.
6. Confirm sufficient free space for the backup and ARM64 image build.
7. Confirm VPS-only secrets and data are not tracked by Git and will not be removed by sync operations.
8. Render Compose and verify `api`, `pipeline-worker`, and `delivery-worker` all mount the same host `./data` directory to `/app/data`. This is mandatory for the cross-container `fcntl.flock` Drive lock.
9. Confirm `/opt/course-transcript/secrets/gcp-sa.json` and the rclone config mounts remain read-only.
10. Stop and report if any prerequisite is not satisfied.

## Build and non-paid validation

After pulling the approved commit without destructive reset:

```bash
cd /opt/course-transcript

python -m compileall -q app tests
python -m unittest discover -s tests -v

sudo docker compose --profile web config --quiet
sudo docker compose --profile billing config --quiet

sudo docker compose --profile web build api worker pipeline-worker delivery-worker frontend
```

Run production-entry import checks inside the newly built image before replacing running services:

```bash
sudo docker compose --profile web run --rm --no-deps api \
  python -c "from app.api_hardened import app; assert app.title"

sudo docker compose --profile web run --rm --no-deps pipeline-worker \
  python -c "import app.pipeline.dynamic_worker_production"

sudo docker compose --profile web run --rm --no-deps delivery-worker \
  python -c "import app.jobs.delivery_worker"
```

These checks must not submit provider work or mutate Drive.

## Service restart

Use the existing Compose/Cloudflare overlay. Do not recreate or alter the tunnel, Access application, DNS, IAM, firewall, or Billing.

```bash
sudo docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web --profile tunnel up -d --build
```

Verify:

- `api`, `worker`, `pipeline-worker`, `delivery-worker`, `frontend`, and `cloudflared` are running;
- API and frontend health checks pass;
- frontend remains bound only to `127.0.0.1:3300`;
- the public Cloudflare route targets `http://frontend:3000`, never `api:8000`;
- the newly running containers report the approved Git/build version when available;
- the SQLite database and existing `data/jobs/` artifacts remain present;
- restarting `pipeline-worker` and `delivery-worker` does not create duplicate paid submissions or Drive writes.

## Drive publication policy

Drive upload is enabled by the hardened design when `COURSE_TRANSCRIPT_AUTO_PUBLISH_TO_SOURCE=true`.

It may publish only selected derived sidecars after local QA. It must use the safe pending/verify/backup/promote/final-verify transaction and the global shared-data lock. Source media must never be overwritten or renamed.

For the first production deployment, real Drive mutation validation must use a disposable test folder and explicit approval.

## Paid acceptance gate

After all non-paid deployment checks pass, stop and request explicit approval for each of the following:

1. one small Chirp 3 dynamic-batch job with the exact source and estimated cost;
2. one real 60-second Gemini correction comparison;
3. one disposable-folder same-name Drive sidecar backup/promote test.

Do not infer approval from the presence of an existing job, page, source file, prior estimate, or prior provider usage.

## Rollback

Rollback uses the pre-deployment source and data backups.

1. Stop `pipeline-worker` and `delivery-worker` first.
2. Preserve submitted operation names, GCS evidence, Gemini audit files, and Drive transaction state.
3. Restore the prior source version without destructive deletion of the project or data directories.
4. Restore SQLite or `data/jobs/` only when required and only from the verified timestamped backup.
5. Rebuild and restart the prior images.
6. Verify health, database state, and restart persistence.

Already submitted provider operations may remain billable and may finish after rollback. Recover them from saved evidence; never re-submit solely because a rollback occurred.

See `docs/VPS_DEPLOY_GATE.md` for the exact operator handoff and evidence format.
