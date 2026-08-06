# Production cutover

Production deployments use an immutable Git SHA release directory and exact-SHA Docker images. Do not deploy from the dirty live working tree.

## Preconditions

- Phase 2C built and validated the approved exact-SHA ARM64 images.
- The approved release SHA is an ancestor of `origin/main`.
- Any commits after the approved release modify only the deployment scripts, their CI workflow, or this runbook. Any application, Dockerfile, or Compose drift blocks deployment.
- `/opt/course-transcript-releases/<SHA>` exists.
- Production data remains at `/opt/course-transcript-source/data`.
- No active or leased jobs exist.
- The delivery candidate diagnosis returns zero eligible jobs.
- The old images, release directories, and prior evidence remain available.

## Step 0: export the reviewed deployment tool

Do not checkout or reset the dirty live working tree. Export only the deployment scripts from the current `origin/main` into a separate tools directory:

```bash
sudo git -C /opt/course-transcript-source fetch --no-tags origin main
TOOLS_SHA="$(sudo git -C /opt/course-transcript-source rev-parse origin/main)"
TOOLS_ROOT="/opt/course-transcript-deploy-tools/${TOOLS_SHA}"
sudo install -d -m 755 "$TOOLS_ROOT"
sudo git -C /opt/course-transcript-source archive origin/main \
  scripts/deploy_release.sh scripts/deploy_release_lib.sh \
  | sudo tar -x -C "$TOOLS_ROOT"
cd "$TOOLS_ROOT"
```

The deployment tool independently verifies that the approved release is an ancestor of `origin/main` and that all later changes are restricted to:

- `.github/workflows/deploy-script.yml`
- `docs/PRODUCTION_CUTOVER.md`
- `scripts/deploy_release.sh`
- `scripts/deploy_release_lib.sh`

## Step 1: dry run

```bash
sudo bash scripts/deploy_release.sh \
  --release-sha a4e4409d8a7ea14d03fb55fd820b003de78c0ad5 \
  --phase2c-evidence /opt/course-transcript-backups/20260806T045443Z-phase2c-build \
  --dry-run
```

The dry run validates release ancestry and the allowlisted main drift, the release archive checksum, protected build evidence, Compose mounts, exact image provenance, job and lease quiescence, delivery candidates, and cloudflared availability. It does not stop, recreate, or restart production containers.

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

Rollback tags, old images, database backups, release directories, deployment tools, and deployment evidence must remain until the production observation period is explicitly closed.
