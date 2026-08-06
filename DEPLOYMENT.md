# Deployment

Production is deployed as an immutable release while persistent state remains outside the release source tree.

Current Oracle ARM64 layout:

- release source: `/opt/course-transcript-releases/<exact-approved-git-sha>`
- persistent state: `/opt/course-transcript-source/{data,logs,tmp}`
- Compose project: `course-transcript-source`
- secrets: `/opt/course-transcript/secrets` and `/home/ubuntu/.config/rclone`

The production deployment is gated. GitHub CI success does not authorize a VPS restart, paid provider request, or Drive mutation.

## Required target

- Branch: `main`
- Deploy only the exact `main` SHA explicitly approved for the VPS gate.
- Do not deploy PR #11 while it remains a prototype Draft.
- API entry point: `app.api_hardened:app`
- pipeline entry point: `app.pipeline.dynamic_worker_production`
- delivery entry point: `app.jobs.delivery_worker`

Use `docker-compose.yml` together with `docker-compose.release.yml`. See `docs/RELEASE_LAYOUT.md` for the required absolute persistent-state paths and exact-SHA image tags.

## Pre-deployment gate

1. Confirm there is no active paid job, no new provider submission in progress, and no non-expired pipeline lease.
2. Record the current deployed Git SHA, image IDs, Compose project, and rendered service commands.
3. Create a timestamped backup directory outside every repository and release worktree.
4. Back up both:
   - `/opt/course-transcript-source/data/course-transcript.db`
   - `/opt/course-transcript-source/data/jobs/`
5. Verify the backup files exist, are non-empty where applicable, and can be listed without exposing secret contents.
6. Confirm sufficient free space for the backup and ARM64 image build.
7. Confirm VPS-only secrets and data are not tracked by Git and will not be removed by release extraction or cleanup.
8. Export the approved commit with `git archive` into a new release directory. Do not reset, clean, checkout, or modify the dirty live source worktree.
9. Render the base and release Compose files together and verify `api`, `worker`, `pipeline-worker`, `delivery-worker`, and `billing-worker` all use the same explicit persistent `/app/data` host source.
10. Confirm GCP, billing, refresh-token, and rclone mounts remain read-only and that the API has no GCP service-account mount.
11. Record any pending Drive delivery state and run candidate selection read-only. Do not call `run_once()` or manually retry.
12. Stop and report if any prerequisite is not satisfied.

## Build and non-paid validation

From the immutable release directory:

```bash
python3 -m compileall -q app tests
python3 -m unittest discover -s tests -v

sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --profile web config --quiet

sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --profile billing config --quiet

sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --profile web build api worker pipeline-worker delivery-worker frontend
```

Run production-entry import checks inside the newly built exact-SHA images before replacing running services:

```bash
sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --profile web run --rm --no-deps api \
  python -c "from app.api_hardened import app; assert app.title"

sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --profile web run --rm --no-deps pipeline-worker \
  python -c "import app.pipeline.dynamic_worker_production"

sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --profile web run --rm --no-deps delivery-worker \
  python -c "import app.jobs.delivery_worker"
```

These checks must not submit provider work or mutate Drive.

## Service replacement

Use the existing Compose project and Cloudflare overlay. Do not recreate or alter the tunnel, Access application, DNS, IAM, firewall, or Billing.

A pending Drive delivery is not an active paid provider job, but it also does not authorize a new Drive mutation. Explain any overdue candidate state before replacing or restarting `delivery-worker`. Other non-paid services may be built and validated independently.

When the deployment window and delivery-worker policy are explicitly approved, replace only the intended services with the base, release, and Cloudflare Compose files under project `course-transcript-source`.

Verify:

- `api`, `worker`, `pipeline-worker`, `frontend`, and any explicitly approved worker replacements are running;
- API and frontend health checks pass;
- frontend remains bound only to `127.0.0.1:3300`;
- the public Cloudflare route targets `http://frontend:3000`, never `api:8000`;
- newly running containers use exact-SHA image tags;
- all services still mount `/opt/course-transcript-source/data` as `/app/data`;
- SQLite and existing `data/jobs/` artifacts remain present;
- no restart creates duplicate paid submissions or unexplained Drive events.

## Drive publication policy

Drive upload is enabled by the hardened design when `COURSE_TRANSCRIPT_AUTO_PUBLISH_TO_SOURCE=true`.

It may publish only selected derived sidecars after local QA. It must use the safe pending/verify/backup/promote/final-verify transaction and the global shared-data lock. Source media must never be overwritten or renamed.

A `pending_retry` item must be diagnosed from `pipeline-manifest.json`, `drive-publish-state.json`, and `drive-delivery-state.json`. If it is overdue but `_candidate()` returns no record, report which selection condition is false. Do not rewrite state to force eligibility.

For the first production mutation validation, use a disposable test folder and explicit approval.

## Paid acceptance gate

After all non-paid deployment checks pass, stop and request explicit approval for each of the following:

1. one small Chirp 3 dynamic-batch job with the exact source and estimated cost;
2. one real correction comparison;
3. one disposable-folder same-name Drive sidecar backup/promote test.

Do not infer approval from the presence of an existing job, page, source file, prior estimate, or prior provider usage.

## Rollback

Rollback uses the prior exact-SHA images plus the verified pre-deployment source and data backups.

1. Stop `pipeline-worker` and `delivery-worker` first when rollback affects provider or Drive processing.
2. Preserve submitted operation names, GCS evidence, correction audit files, and Drive transaction state.
3. Restore the prior exact-SHA image set without deleting the persistent state directories.
4. Restore SQLite or `data/jobs/` only when required and only from the verified timestamped backup.
5. Verify health, database state, and restart persistence.

Already submitted provider operations may remain billable and may finish after rollback. Recover them from saved evidence; never re-submit solely because a rollback occurred.

See `docs/RELEASE_LAYOUT.md` and `docs/VPS_DEPLOY_GATE.md` for operator evidence requirements.
