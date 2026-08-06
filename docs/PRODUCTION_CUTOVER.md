# Production cutover

Production deployments use an immutable Git SHA release directory and exact-SHA Docker images. Do not deploy from the dirty live working tree.

## Preconditions

- The approved SHA is the current `origin/main`.
- Phase 2C built and validated the exact-SHA ARM64 images.
- `/opt/course-transcript-releases/<SHA>` exists.
- Production data remains at `/opt/course-transcript-source/data`.
- No active or leased jobs exist.
- The delivery candidate diagnosis returns zero eligible jobs.
- The old images, release directories, and prior evidence remain available.

## Step 1: dry run

```bash
sudo bash scripts/deploy_release.sh \
  --release-sha a4e4409d8a7ea14d03fb55fd820b003de78c0ad5 \
  --phase2c-evidence /opt/course-transcript-backups/20260806T045443Z-phase2c-build \
  --dry-run
```

The dry run validates the release SHA and archive checksum, protected build evidence, Compose mounts, exact image provenance, job and lease quiescence, delivery candidates, and cloudflared availability. It does not stop, recreate, or restart production containers.

## Step 2: execute

Execute only after the dry run reports `PHASE2D_DRY_RUN=PASS`:

```bash
sudo bash scripts/deploy_release.sh \
  --release-sha a4e4409d8a7ea14d03fb55fd820b003de78c0ad5 \
  --phase2c-evidence /opt/course-transcript-backups/20260806T045443Z-phase2c-build \
  --execute \
  --confirm-sha a4e4409d8a7ea14d03fb55fd820b003de78c0ad5
```

The script creates rollback image tags and a consistent SQLite backup, stops the frontend to freeze new submissions, rechecks all safety gates, replaces the API and workers in order, proves the delivery worker remains idle, restores the frontend, validates local and public routes, and confirms cloudflared was not recreated. A failed gate automatically rolls all five application services back to the captured images.

## Safety constraints

The script never runs `docker compose down`, `docker compose build`, `docker compose restart`, `docker system prune`, or `--remove-orphans`. It does not modify SQLite job records, delivery state, `.env`, rclone credentials, cloudflared, or PR #11.

Rollback tags, old images, database backups, release directories, and deployment evidence must remain until the production observation period is explicitly closed.
