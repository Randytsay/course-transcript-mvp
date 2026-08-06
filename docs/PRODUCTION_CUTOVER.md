# Production cutover

Production deployments use an immutable Git SHA release directory and exact-SHA Docker images. The dirty live working tree is not a release source and must not be checked out, reset, stashed, cleaned, or otherwise modified during deployment.

## Preconditions

- Phase 2C built and validated the approved exact-SHA ARM64 images.
- The approved release SHA is an ancestor of `origin/main`.
- Any commits after the approved release modify only the reviewed deployment tools, their tests, CI workflow, or deployment documentation. Any application, Dockerfile, frontend, or Compose drift blocks deployment.
- `/opt/course-transcript-releases/<SHA>` exists.
- Production data remains at `/opt/course-transcript-source/data`.
- No active or leased jobs exist.
- The delivery candidate diagnosis returns zero eligible jobs.
- The old images, release directories, rollback tags, and prior evidence remain available.
- Existing live working-tree modifications are recorded as evidence but do not block an immutable release. They must remain untouched.

## Step 0: export the reviewed deployment tool

Do not checkout or reset the live working tree. Export only the deployment tools from the current `origin/main` into a separate directory:

```bash
sudo git -C /opt/course-transcript-source fetch --no-tags origin main
TOOLS_SHA="$(sudo git -C /opt/course-transcript-source rev-parse origin/main)"
TOOLS_ROOT="/opt/course-transcript-deploy-tools/${TOOLS_SHA}"
sudo install -d -m 755 "$TOOLS_ROOT"
sudo git -C /opt/course-transcript-source archive origin/main \
  scripts/deploy_release.sh \
  scripts/deploy_release_lib.sh \
  scripts/deploy_release_safe.sh \
  scripts/scan_evidence_credentials.py \
  | sudo tar -x -C "$TOOLS_ROOT"
cd "$TOOLS_ROOT"
```

The safe entrypoint creates a temporary, syntax-checked copy of the deployment script. It applies two exact, fail-closed compatibility updates:

1. it extends the post-release allowlist to the reviewed deployment-only files;
2. it replaces the ambiguous recursive `grep -c` evidence scan with the deterministic Python scanner, which prints only a numeric count and never matched content.

The approved release may differ from `origin/main` only in:

- `.github/workflows/deploy-script.yml`
- `docs/PRODUCTION_CUTOVER.md`
- `docs/PHASE2D_CREDENTIAL_SCAN_INCIDENT.md`
- `scripts/deploy_release.sh`
- `scripts/deploy_release_lib.sh`
- `scripts/deploy_release_safe.sh`
- `scripts/scan_evidence_credentials.py`
- `tests/test_deploy_release_safe.sh`

## Step 1: dry run

```bash
sudo bash scripts/deploy_release_safe.sh \
  --release-sha a4e4409d8a7ea14d03fb55fd820b003de78c0ad5 \
  --phase2c-evidence /opt/course-transcript-backups/20260806T045443Z-phase2c-build \
  --dry-run
```

The dry run validates release ancestry and allowlisted main drift, the release archive checksum, protected build evidence, Compose mounts, exact image provenance, job and lease quiescence, delivery candidates, and cloudflared availability. It does not stop, recreate, or restart production containers.

## Step 2: execute

Execute only after the safe dry run reports `PHASE2D_DRY_RUN=PASS`:

```bash
sudo bash scripts/deploy_release_safe.sh \
  --release-sha a4e4409d8a7ea14d03fb55fd820b003de78c0ad5 \
  --phase2c-evidence /opt/course-transcript-backups/20260806T045443Z-phase2c-build \
  --execute \
  --confirm-sha a4e4409d8a7ea14d03fb55fd820b003de78c0ad5
```

The script creates rollback image tags and a consistent SQLite backup, stops the frontend to freeze new submissions, rechecks all safety gates, replaces the API and workers in order, proves the delivery worker remains idle, restores the frontend, validates local and public routes, and confirms cloudflared was not recreated. A failed gate automatically rolls all five application services back to the captured images.

## Safety constraints

The script never runs `docker compose down`, `docker compose build`, `docker compose restart`, `docker system prune`, or `--remove-orphans`. It does not modify SQLite job records, delivery state, `.env`, rclone credentials, cloudflared, the live working tree, or PR #11.

Rollback tags, old images, database backups, release directories, deployment tools, and deployment evidence must remain until the production observation period is explicitly closed.
