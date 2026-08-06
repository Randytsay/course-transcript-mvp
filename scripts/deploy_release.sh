#!/usr/bin/env bash
set -Eeuo pipefail

PROGRAM="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="dry-run"
RELEASE_SHA=""
CONFIRM_SHA=""
EVIDENCE_ROOT=""
PHASE2C_EVIDENCE_ARG=""
AUDIT_JOB_ID_ARG=""
PUBLIC_ORIGIN_ARG=""
SELF_TEST=0

usage() {
  cat <<USAGE
Usage:
  $PROGRAM --release-sha <40-char-sha> [--dry-run]
  $PROGRAM --release-sha <40-char-sha> --execute --confirm-sha <same-sha>
  $PROGRAM --self-test

Modes:
  --dry-run       Validate the immutable release, images, production state, and
                  rollback prerequisites without changing live containers.
  --execute       Perform the production cutover. Requires --confirm-sha.

Options:
  --evidence-root <path>   Override the evidence directory.
  --phase2c-evidence <dir> Override the Phase 2C evidence directory.
  --audit-job-id <id>      Override the stale delivery job used for audit.
  --public-origin <url>    Override the production public origin.
  --help                   Show this help.
USAGE
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --release-sha)
      [[ $# -ge 2 ]] || fail "--release-sha requires a value"
      RELEASE_SHA="$2"
      shift 2
      ;;
    --confirm-sha)
      [[ $# -ge 2 ]] || fail "--confirm-sha requires a value"
      CONFIRM_SHA="$2"
      shift 2
      ;;
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --execute)
      MODE="execute"
      shift
      ;;
    --evidence-root)
      [[ $# -ge 2 ]] || fail "--evidence-root requires a value"
      EVIDENCE_ROOT="$2"
      shift 2
      ;;
    --phase2c-evidence)
      [[ $# -ge 2 ]] || fail "--phase2c-evidence requires a value"
      PHASE2C_EVIDENCE_ARG="$2"
      shift 2
      ;;
    --audit-job-id)
      [[ $# -ge 2 ]] || fail "--audit-job-id requires a value"
      AUDIT_JOB_ID_ARG="$2"
      shift 2
      ;;
    --public-origin)
      [[ $# -ge 2 ]] || fail "--public-origin requires a value"
      PUBLIC_ORIGIN_ARG="$2"
      shift 2
      ;;
    --self-test)
      SELF_TEST=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

if ((SELF_TEST)); then
  [[ "$MODE" == "dry-run" ]]
  [[ -z "$RELEASE_SHA" ]]
  printf 'SELF_TEST=PASS\n'
  exit 0
fi

[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "release SHA must be 40 lowercase hex characters"
if [[ "$MODE" == "execute" ]]; then
  [[ "$CONFIRM_SHA" == "$RELEASE_SHA" ]] || fail "--execute requires --confirm-sha matching the release SHA"
fi
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "run as root, for example: sudo bash $PROGRAM ..."

PROJECT="course-transcript-source"
SOURCE_REPO="/opt/course-transcript-source"
RELEASE_ROOT="/opt/course-transcript-releases/${RELEASE_SHA}"
DATA_ROOT="/opt/course-transcript-source/data"
LOGS_ROOT="/opt/course-transcript-source/logs"
TMP_ROOT="/opt/course-transcript-source/tmp"
DB_PATH="${DATA_ROOT}/course-transcript.db"
PUBLIC_ORIGIN="${PUBLIC_ORIGIN_ARG:-https://transcript.randy88.ccwu.cc}"
PHASE2C_EVIDENCE="${PHASE2C_EVIDENCE_ARG:-/opt/course-transcript-backups/20260806T045443Z-phase2c-build}"
BUILD_ENV="${PHASE2C_EVIDENCE}/.env.phase2c-build"
STALE_JOB_ID="${AUDIT_JOB_ID_ARG:-2026-0801-20260803-030453-1db3de}"
STALE_JOB_DIR="${DATA_ROOT}/jobs/${STALE_JOB_ID}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ROLLBACK_TAG="rollback-${STAMP}"

if [[ -z "$EVIDENCE_ROOT" ]]; then
  EVIDENCE_ROOT="/opt/course-transcript-backups/${STAMP}-phase2d-${MODE}"
fi
install -d -m 700 "$EVIDENCE_ROOT"
exec > >(tee -a "$EVIDENCE_ROOT/deploy.log") 2>&1

printf 'PHASE2D_MODE=%s\nrelease_sha=%s\nevidence_root=%s\n' \
  "$MODE" "$RELEASE_SHA" "$EVIDENCE_ROOT"

TMP_FILES=()
ROLLBACK_ARMED=0
ROLLBACK_RUNNING=0

cleanup_temps() {
  local path
  for path in "${TMP_FILES[@]:-}"; do
    [[ -n "$path" ]] && rm -f -- "$path" || true
  done
}

export COURSE_TRANSCRIPT_RELEASE_TAG="$RELEASE_SHA"
export COURSE_TRANSCRIPT_ENV_FILE="/home/ubuntu/.env"
export COURSE_TRANSCRIPT_TARGETARCH="arm64"
export RCLONE_RELEASE="1.74.0"
export COURSE_TRANSCRIPT_DATA_HOST_PATH="$DATA_ROOT"
export COURSE_TRANSCRIPT_LOGS_HOST_PATH="$LOGS_ROOT"
export COURSE_TRANSCRIPT_TMP_HOST_PATH="$TMP_ROOT"
export GCP_CREDENTIALS_HOST_PATH="/opt/course-transcript/secrets/gcp-sa.json"
export RCLONE_CONFIG_HOST_PATH="/home/ubuntu/.config/rclone/rclone.conf"
export GOOGLE_DRIVE_REFRESH_TOKEN_HOST_PATH="/dev/null"
export BILLING_CREDENTIALS_HOST_PATH="/opt/course-transcript/secrets/billing-sa.json"
export DOCKER_DEFAULT_PLATFORM="linux/arm64"

compose() {
  docker compose \
    --project-name "$PROJECT" \
    --env-file /home/ubuntu/.env \
    -f "$RELEASE_ROOT/docker-compose.yml" \
    -f "$RELEASE_ROOT/docker-compose.release.yml" \
    --profile web \
    "$@"
}

declare -A LIVE_CONTAINERS=(
  [api]="course-transcript-source-api-1"
  [worker]="course-transcript-source-worker-1"
  [pipeline-worker]="course-transcript-source-pipeline-worker-1"
  [delivery-worker]="course-transcript-source-delivery-worker-1"
  [frontend]="course-transcript-source-frontend-1"
)

declare -A IMAGE_REPOS=(
  [api]="course-transcript-api"
  [worker]="course-transcript-worker"
  [pipeline-worker]="course-transcript-pipeline-worker"
  [delivery-worker]="course-transcript-delivery-worker"
  [frontend]="course-transcript-frontend"
)

declare -A NEW_IMAGES=(
  [api]="course-transcript-api:${RELEASE_SHA}"
  [worker]="course-transcript-worker:${RELEASE_SHA}"
  [pipeline-worker]="course-transcript-pipeline-worker:${RELEASE_SHA}"
  [delivery-worker]="course-transcript-delivery-worker:${RELEASE_SHA}"
  [frontend]="course-transcript-frontend:${RELEASE_SHA}"
)

CLOUDFLARED_CONTAINER="course-transcript-cloudflared-1"

# shellcheck source=scripts/deploy_release_lib.sh
source "$SCRIPT_DIR/deploy_release_lib.sh"
trap on_exit EXIT

# Common preflight.
git -C "$SOURCE_REPO" fetch --no-tags origin main
ORIGIN_MAIN="$(git -C "$SOURCE_REPO" rev-parse origin/main)"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$RELEASE_SHA" "$ORIGIN_MAIN" \
  || fail "approved release is not an ancestor of origin/main"

mapfile -t MAIN_DRIFT_PATHS < <(
  git -C "$SOURCE_REPO" diff --name-only "$RELEASE_SHA" "$ORIGIN_MAIN"
)
for path in "${MAIN_DRIFT_PATHS[@]:-}"; do
  [[ -z "$path" ]] && continue
  case "$path" in
    .github/workflows/deploy-script.yml|docs/PRODUCTION_CUTOVER.md|scripts/deploy_release.sh|scripts/deploy_release_lib.sh)
      ;;
    *)
      fail "origin/main contains non-deployment changes after approved release: $path"
      ;;
  esac
done
printf 'origin_main=%s\napproved_release=%s\nallowed_drift_count=%s\n' \
  "$ORIGIN_MAIN" "$RELEASE_SHA" "${#MAIN_DRIFT_PATHS[@]}" \
  > "$EVIDENCE_ROOT/main-compatibility.txt"
printf '%s\n' "${MAIN_DRIFT_PATHS[@]:-}" \
  >> "$EVIDENCE_ROOT/main-compatibility.txt"

[[ -d "$RELEASE_ROOT" ]] || fail "release root missing: $RELEASE_ROOT"
[[ -f "$RELEASE_ROOT/docker-compose.yml" ]] || fail "release compose missing"
[[ -f "$RELEASE_ROOT/docker-compose.release.yml" ]] || fail "release override missing"
[[ ! -e "$RELEASE_ROOT/app/providers/rechunk_single.py" ]] || fail "PR #11 prototype exists in release"
[[ -f /home/ubuntu/.env ]] || fail "production env file missing"
[[ -f /home/ubuntu/.config/rclone/rclone.conf ]] || fail "rclone config missing"
[[ -f "$DB_PATH" ]] || fail "production database missing"

RELEASE_ARCHIVE_CHECKSUM="$(git -C "$SOURCE_REPO" archive "$RELEASE_SHA" | sha256sum | awk '{print $1}')"
printf '%s  git-archive:%s\n' "$RELEASE_ARCHIVE_CHECKSUM" "$RELEASE_SHA" \
  > "$EVIDENCE_ROOT/release-archive.sha256"
if [[ -f "$PHASE2C_EVIDENCE/release-archive.sha256" ]]; then
  PHASE2C_ARCHIVE_CHECKSUM="$(awk 'NR == 1 {print $1}' "$PHASE2C_EVIDENCE/release-archive.sha256")"
  [[ "$PHASE2C_ARCHIVE_CHECKSUM" == "$RELEASE_ARCHIVE_CHECKSUM" ]] \
    || fail "release archive checksum differs from Phase 2C"
fi

audit_build_env "$EVIDENCE_ROOT/phase2c-build-env-audit.txt"
validate_compose_render | tee "$EVIDENCE_ROOT/compose-validation.txt"
validate_images | tee "$EVIDENCE_ROOT/image-validation.txt"
check_quiescent_jobs "$EVIDENCE_ROOT/jobs-preflight.json"
validate_delivery_candidates "$EVIDENCE_ROOT/delivery-candidates-preflight.jsonl" \
  | tee "$EVIDENCE_ROOT/delivery-candidate-validation.txt"

docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}' \
  "$CLOUDFLARED_CONTAINER" > "$EVIDENCE_ROOT/cloudflared-before.txt"
[[ "$(docker inspect --format '{{.State.Status}}' "$CLOUDFLARED_CONTAINER")" == "running" ]] \
  || fail "cloudflared is not running"

if [[ "$MODE" == "dry-run" ]]; then
  cat > "$EVIDENCE_ROOT/result.txt" <<RESULT
PHASE2D_DRY_RUN=PASS
release_sha=${RELEASE_SHA}
release_archive_checksum=${RELEASE_ARCHIVE_CHECKSUM}
origin_main_compatible=YES
origin_main=${ORIGIN_MAIN}
allowed_main_drift_count=${#MAIN_DRIFT_PATHS[@]}
compose_render=PASS
images=PASS
active_jobs=0
live_leases=0
eligible_delivery_candidates=0
cloudflared_running=YES
CUTOVER_COMMAND=sudo bash scripts/deploy_release.sh --release-sha ${RELEASE_SHA} --execute --confirm-sha ${RELEASE_SHA}
RESULT
  cat "$EVIDENCE_ROOT/result.txt"
  exit 0
fi

# Capture current production identity and create rollback artifacts.
printf 'service|container_id|image_id|started_at|restart_count\n' > "$EVIDENCE_ROOT/live-before.txt"
for service in api worker pipeline-worker delivery-worker frontend; do
  container="${LIVE_CONTAINERS[$service]}"
  docker inspect "$container" >/dev/null
  printf '%s|%s|%s|%s|%s\n' \
    "$service" \
    "$(docker inspect --format '{{.Id}}' "$container")" \
    "$(docker inspect --format '{{.Image}}' "$container")" \
    "$(docker inspect --format '{{.State.StartedAt}}' "$container")" \
    "$(docker inspect --format '{{.RestartCount}}' "$container")" \
    >> "$EVIDENCE_ROOT/live-before.txt"
  rollback_image="${IMAGE_REPOS[$service]}:${ROLLBACK_TAG}"
  ! docker image inspect "$rollback_image" >/dev/null 2>&1 || fail "rollback tag exists: $rollback_image"
  docker tag "$(docker inspect --format '{{.Image}}' "$container")" "$rollback_image"
done
printf '%s\n' "$ROLLBACK_TAG" > "$EVIDENCE_ROOT/rollback-tag.txt"

DB_BACKUP="$EVIDENCE_ROOT/course-transcript-pre-cutover.sqlite3"
python3 - "$DB_PATH" "$DB_BACKUP" <<'PY'
import sqlite3
import sys
source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
destination = sqlite3.connect(sys.argv[2])
try:
    source.backup(destination)
    result = destination.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise SystemExit(f"backup integrity check failed: {result}")
finally:
    destination.close()
    source.close()
PY
chmod 600 "$DB_BACKUP"
sha256sum "$DB_BACKUP" > "$EVIDENCE_ROOT/database-backup.sha256"
ROLLBACK_ARMED=1

# Freeze the UI, then recheck state before replacing workers.
compose stop --timeout 20 frontend
[[ "$(docker inspect --format '{{.State.Status}}' "${LIVE_CONTAINERS[frontend]}")" == "exited" ]] \
  || fail "frontend did not stop cleanly"
[[ "$(docker inspect --format '{{.State.Status}}' "$CLOUDFLARED_CONTAINER")" == "running" ]] \
  || fail "cloudflared changed during frontend freeze"
check_quiescent_jobs "$EVIDENCE_ROOT/jobs-after-freeze.json"
validate_delivery_candidates "$EVIDENCE_ROOT/delivery-candidates-after-freeze.jsonl" >/dev/null

# Cut over API.
API_SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
compose up -d --no-build --no-deps --force-recreate api
verify_exact_service api
wait_health "${LIVE_CONTAINERS[api]}" || fail "new API is unhealthy"
docker exec "${LIVE_CONTAINERS[api]}" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).read()"

# Cut over non-HTTP workers.
for service in worker pipeline-worker; do
  check_quiescent_jobs "$EVIDENCE_ROOT/jobs-before-${service}.json"
  since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  compose up -d --no-build --no-deps --force-recreate "$service"
  verify_exact_service "$service"
  verify_worker_stable "$service" "$since" || fail "new $service is unstable"
done
check_quiescent_jobs "$EVIDENCE_ROOT/jobs-after-pipeline-worker.json"

# Cut over delivery worker last and prove it remains idle.
STATE_BEFORE="$EVIDENCE_ROOT/delivery-state-before.txt"
STATE_AFTER="$EVIDENCE_ROOT/delivery-state-after.txt"
delivery_state_snapshot "$STATE_BEFORE"
DELIVERY_SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
compose up -d --no-build --no-deps --force-recreate delivery-worker
verify_exact_service delivery-worker
verify_worker_stable delivery-worker "$DELIVERY_SINCE" || fail "new delivery-worker is unstable"
sleep 70
DELIVERY_MARKERS="$(docker logs --since "$DELIVERY_SINCE" "${LIVE_CONTAINERS[delivery-worker]}" 2>&1 \
  | grep -Ec 'DRIVE_DELIVERY=(PASS|RETRY|LOCK_RETRY|SKIP_EDITOR_OWNED)' || true)"
[[ "$DELIVERY_MARKERS" == "0" ]] || fail "delivery-worker unexpectedly selected work"
validate_delivery_candidates "$EVIDENCE_ROOT/delivery-candidates-after-cutover.jsonl" >/dev/null
delivery_state_snapshot "$STATE_AFTER"
cmp -s "$STATE_BEFORE" "$STATE_AFTER" || fail "stale delivery state changed"

# Restore the UI only after all backends pass.
compose up -d --no-build --no-deps --force-recreate frontend
verify_exact_service frontend
wait_health "${LIVE_CONTAINERS[frontend]}" || fail "new frontend is unhealthy"
curl --fail --silent --show-error --max-time 15 http://127.0.0.1:3300/ >/dev/null
curl --fail --silent --show-error --max-time 15 http://127.0.0.1:3300/api/v1/health >/dev/null

PUBLIC_CODE="$(curl --silent --show-error --max-time 20 --output /dev/null --write-out '%{http_code}' "$PUBLIC_ORIGIN")"
case "$PUBLIC_CODE" in
  200|301|302|303|307|308|401|403) ;;
  *) fail "unexpected public HTTP status: $PUBLIC_CODE" ;;
esac

docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}' \
  "$CLOUDFLARED_CONTAINER" > "$EVIDENCE_ROOT/cloudflared-after.txt"
cmp -s "$EVIDENCE_ROOT/cloudflared-before.txt" "$EVIDENCE_ROOT/cloudflared-after.txt" \
  || fail "cloudflared container changed"

# Final verification.
printf 'service|container_id|image_id|state|health|restart_count|revision\n' > "$EVIDENCE_ROOT/live-after.txt"
for service in api worker pipeline-worker delivery-worker frontend; do
  verify_exact_service "$service"
  container="${LIVE_CONTAINERS[$service]}"
  state="$(docker inspect --format '{{.State.Status}}' "$container")"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-defined{{end}}' "$container")"
  restarts="$(docker inspect --format '{{.RestartCount}}' "$container")"
  revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${NEW_IMAGES[$service]}")"
  [[ "$restarts" == "0" ]] || fail "$service restart count is not zero"
  [[ "$revision" == "$RELEASE_SHA" ]] || fail "$service revision mismatch"
  if [[ "$service" == "api" || "$service" == "frontend" ]]; then
    [[ "$health" == "healthy" ]] || fail "$service final health is not healthy"
  fi
  printf '%s|%s|%s|%s|%s|%s|%s\n' \
    "$service" \
    "$(docker inspect --format '{{.Id}}' "$container")" \
    "$(docker inspect --format '{{.Image}}' "$container")" \
    "$state" "$health" "$restarts" "$revision" \
    >> "$EVIDENCE_ROOT/live-after.txt"
done
check_quiescent_jobs "$EVIDENCE_ROOT/jobs-final.json"

for service in api worker pipeline-worker delivery-worker frontend; do
  count="$(docker logs --since "$API_SINCE" "${LIVE_CONTAINERS[$service]}" 2>&1 \
    | grep -Eic 'Traceback|ModuleNotFoundError|ImportError|CRITICAL|FATAL|permission_denied' || true)"
  printf '%s_error_pattern_count=%s\n' "$service" "$count" >> "$EVIDENCE_ROOT/final-log-scan.txt"
  [[ "$count" == "0" ]] || fail "final log scan failed for $service"
done

credential_markers="$(grep -ERic \
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|"refresh_token"[[:space:]]*:|"client_secret"[[:space:]]*:|Authorization: Bearer|Cf-Access-Jwt-Assertion' \
  "$EVIDENCE_ROOT" || true)"
printf 'evidence_raw_credential_marker_count=%s\n' "$credential_markers" >> "$EVIDENCE_ROOT/final-log-scan.txt"
[[ "$credential_markers" == "0" ]] || fail "possible credential content in evidence"

ROLLBACK_ARMED=0
trap - EXIT
cleanup_temps
cat > "$EVIDENCE_ROOT/result.txt" <<RESULT
PHASE2D_RESULT=PASS
main_exact_sha=${RELEASE_SHA}
release_archive_checksum=${RELEASE_ARCHIVE_CHECKSUM}
origin_main_compatible=YES
origin_main=${ORIGIN_MAIN}
allowed_main_drift_count=${#MAIN_DRIFT_PATHS[@]}
rollback_tag=${ROLLBACK_TAG}
database_backup=${DB_BACKUP}
api_health=healthy
worker_stable=YES
pipeline_worker_stable=YES
delivery_worker_stable=YES
delivery_marker_count=0
eligible_delivery_candidates=0
stale_delivery_state_changed=NO
frontend_health=healthy
frontend_api_proxy=PASS
public_http_code=${PUBLIC_CODE}
cloudflared_changed=NO
provider_calls_made=NO
drive_mutations_made=NO
PRODUCTION_CUTOVER_COMPLETE=YES
OBSERVATION_READY=YES
OLD_IMAGES_DELETE_READY=NO
evidence_root=${EVIDENCE_ROOT}
RESULT
cat "$EVIDENCE_ROOT/result.txt"
