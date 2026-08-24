# VPS Deploy Gate for Hermes — Immutable Release Layout

This document is the non-paid deployment contract for the Oracle ARM64 production host.

It supersedes the older single-directory `/opt/course-transcript` workflow. That path may still exist as a historical snapshot, but it is **not** the production Git checkout and must not be used as a deployment source.

Current layout:

- Git/source checkout: `/opt/course-transcript-source`
- persistent state: `/opt/course-transcript-source/{data,logs,tmp}`
- immutable releases: `/opt/course-transcript-releases/<exact-approved-git-sha>`
- protected service environment: `/home/ubuntu/.env`
- Compose project: `course-transcript-source`
- secrets: `/opt/course-transcript/secrets` and `/home/ubuntu/.config/rclone`

`DEPLOYMENT.md` and `docs/RELEASE_LAYOUT.md` define the same immutable-release architecture. If this document and the observed host layout disagree, stop and report rather than inventing a third deployment method.

## Operating rules

Hermes must:

- never use `/opt/course-transcript` as the Git deployment source;
- never checkout/reset/clean the live source worktree merely to deploy a release;
- never use `git reset --hard`, `git clean -fd`, or destructive deletion on source/data/release roots;
- never print service-account JSON, rclone contents, OAuth tokens, MiniMax keys, Cloudflare tokens, or protected env values;
- never change Cloudflare Access, DNS, tunnel policy, IAM, firewall, Billing, or GCP quota;
- never infer paid-provider approval from a prior job, file, page, or earlier validation;
- never re-submit a retained Chirp operation merely because output is pending;
- stop on active paid work, a non-expired pipeline lease, failed backup, release/source mismatch, shared-volume mismatch, image-provenance mismatch, or unexplained production state.

## Required inputs

```text
APPROVED_GIT_SHA=<exact current main SHA>
DEPLOYMENT_WINDOW_APPROVED=yes
PAID_ACCEPTANCE_APPROVED=no
APPROVED_SERVICES=<comma-separated services allowed to be replaced>
```

`APPROVED_GIT_SHA` is intentionally strict. During Phase C the freshly fetched `origin/main` **must equal** this SHA. If main advanced after approval, report `GATE_STOP: STALE_APPROVAL` and request a new exact-SHA approval. Never deploy an older approved SHA over a service already running a newer revision.

Unless a separate paid gate is explicitly approved, this document performs no Chirp, Gemini, MiniMax, Drive, or YouTube mutation.

---

## Phase A — Read-only inventory

```bash
set -euo pipefail

SOURCE_REPO=/opt/course-transcript-source
DATA_ROOT=/opt/course-transcript-source/data
RELEASE_ROOT=/opt/course-transcript-releases
PROJECT=course-transcript-source
ENV_FILE=/home/ubuntu/.env

for path in "$SOURCE_REPO" "$DATA_ROOT" "$RELEASE_ROOT"; do
  test -e "$path" || { echo "GATE_STOP: missing $path"; exit 1; }
done

git -C "$SOURCE_REPO" rev-parse --is-inside-work-tree
echo "source_local_head=$(git -C "$SOURCE_REPO" rev-parse HEAD)"
echo "source_branch=$(git -C "$SOURCE_REPO" branch --show-current)"
echo "architecture=$(uname -m)"
docker --version
docker compose version
df -h /opt | tail -n 1
```

Record the current Compose project and per-service image revision without printing environment values:

```bash
sudo docker ps \
  --filter "label=com.docker.compose.project=${PROJECT}" \
  --format '{{.ID}} {{.Names}} {{.Label "com.docker.compose.service"}}' \
| while read -r cid name service; do
    revision="$(sudo docker inspect "$cid" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
    config_files="$(sudo docker inspect "$cid" --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}')"
    printf 'running_service=%s container=%s revision=%s config_files=%s\n' \
      "$service" "$name" "${revision:-UNKNOWN}" "$config_files"
  done
```

Mixed current revisions are inventory evidence, not automatic failure. They must be reported so the replacement scope cannot accidentally downgrade a newer service.

Inspect the production database read-only:

```bash
python3 - <<'PY'
from __future__ import annotations
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

path = Path('/opt/course-transcript-source/data/course-transcript.db')
if not path.is_file():
    raise SystemExit('GATE_FAIL: database missing')

now = datetime.now(UTC).isoformat()
connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
try:
    rows = connection.execute(
        '''
        SELECT id, status, active_stage, approved_at, reserved_cost_usd,
               locked_by, lease_expires_at, updated_at
        FROM jobs
        WHERE status NOT IN ('completed', 'cancelled', 'failed', 'awaiting_review')
           OR (lease_expires_at IS NOT NULL AND lease_expires_at >= ?)
        ORDER BY updated_at
        ''',
        (now,),
    ).fetchall()
finally:
    connection.close()

print(f'active_or_leased_jobs={len(rows)}')
for row in rows:
    print(
        'job', row['id'],
        f"status={row['status']}",
        f"stage={row['active_stage']}",
        f"locked_by={row['locked_by']}",
        f"lease_expires_at={row['lease_expires_at']}",
    )
if rows:
    raise SystemExit('GATE_STOP: active or leased jobs require human review')
PY
```

Also check whether the currently running delivery worker sees an actionable Drive retry **without calling `run_once()`**:

```bash
DELIVERY_CID="$(sudo docker ps \
  --filter "label=com.docker.compose.project=${PROJECT}" \
  --filter 'label=com.docker.compose.service=delivery-worker' \
  -q | head -n 1)"

if [ -n "$DELIVERY_CID" ]; then
  sudo docker exec "$DELIVERY_CID" python - <<'PY'
from app.jobs.delivery_worker import _candidate
row = _candidate()
print('drive_delivery_candidate=' + ('NONE' if row is None else str(row.get('id'))))
PY
fi
```

If an actionable Drive candidate exists, do not restart/replace `delivery-worker` in this gate. Report it for separate approval.

---

## Phase B — Verified backup

Back up persistent state, not the historical `/opt/course-transcript` snapshot.

```bash
set -euo pipefail
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="/opt/course-transcript-backups/${STAMP}"
DATA_ROOT=/opt/course-transcript-source/data

sudo mkdir -p "$BACKUP_ROOT"
sudo chown "$(id -u):$(id -g)" "$BACKUP_ROOT"

cp -a "$DATA_ROOT/course-transcript.db" "$BACKUP_ROOT/course-transcript.db"
cp -a "$DATA_ROOT/jobs" "$BACKUP_ROOT/jobs"

test -s "$BACKUP_ROOT/course-transcript.db"
test -d "$BACKUP_ROOT/jobs"
sha256sum "$BACKUP_ROOT/course-transcript.db" > "$BACKUP_ROOT/course-transcript.db.sha256"
find "$BACKUP_ROOT/jobs" -type f | wc -l > "$BACKUP_ROOT/jobs-file-count.txt"

echo "backup_root=$BACKUP_ROOT"
cat "$BACKUP_ROOT/course-transcript.db.sha256"
cat "$BACKUP_ROOT/jobs-file-count.txt"
```

Do not print artifact contents.

---

## Phase C — Fresh remote verification and immutable release creation

Do **not** merge or checkout the live source worktree. Fetch refs only, then archive the exact approved commit.

```bash
set -euo pipefail
SOURCE_REPO=/opt/course-transcript-source
RELEASE_ROOT=/opt/course-transcript-releases

: "${APPROVED_GIT_SHA:?APPROVED_GIT_SHA is required}"

git -C "$SOURCE_REPO" fetch --prune origin main
REMOTE_MAIN="$(git -C "$SOURCE_REPO" rev-parse origin/main)"
echo "origin_main=$REMOTE_MAIN"

if [ "$REMOTE_MAIN" != "$APPROVED_GIT_SHA" ]; then
  echo "GATE_STOP: STALE_APPROVAL approved=$APPROVED_GIT_SHA origin_main=$REMOTE_MAIN"
  exit 1
fi

git -C "$SOURCE_REPO" cat-file -e "${APPROVED_GIT_SHA}^{commit}"
echo 'source_worktree_status_begin'
git -C "$SOURCE_REPO" status --short || true
echo 'source_worktree_status_end'

RELEASE_DIR="$RELEASE_ROOT/$APPROVED_GIT_SHA"

if [ -d "$RELEASE_DIR" ]; then
  VERIFY_DIR="$(mktemp -d /tmp/course-transcript-release-verify.XXXXXX)"
  git -C "$SOURCE_REPO" archive "$APPROVED_GIT_SHA" | tar -x -C "$VERIFY_DIR"
  if ! diff -qr --exclude=DEPLOYED_GIT_SHA "$VERIFY_DIR" "$RELEASE_DIR" >/tmp/course-release-diff.txt; then
    echo 'GATE_STOP: existing immutable release differs from approved Git archive'
    sed -n '1,40p' /tmp/course-release-diff.txt
    find "$VERIFY_DIR" -mindepth 1 -delete
    rmdir "$VERIFY_DIR"
    exit 1
  fi
  find "$VERIFY_DIR" -mindepth 1 -delete
  rmdir "$VERIFY_DIR"
  rm -f /tmp/course-release-diff.txt
  echo 'release_source=EXISTING_VERIFIED'
else
  mkdir -p "$RELEASE_DIR"
  git -C "$SOURCE_REPO" archive "$APPROVED_GIT_SHA" | tar -x -C "$RELEASE_DIR"
  printf '%s\n' "$APPROVED_GIT_SHA" > "$RELEASE_DIR/DEPLOYED_GIT_SHA"
  echo 'release_source=CREATED_FROM_GIT_ARCHIVE'
fi

printf 'release_dir=%s\n' "$RELEASE_DIR"
test -f "$RELEASE_DIR/docker-compose.yml"
test -f "$RELEASE_DIR/docker-compose.release.yml"
test -f "$RELEASE_DIR/Dockerfile"
```

The local source checkout may be dirty; that is not a deployment blocker because no local working-tree content is copied. The exact Git object from `origin/main` is the source of truth.

---

## Phase D — Render immutable release and verify mounts/provenance

```bash
set -euo pipefail
RELEASE_DIR="/opt/course-transcript-releases/${APPROVED_GIT_SHA}"
cd "$RELEASE_DIR"

export COURSE_TRANSCRIPT_RELEASE_TAG="$APPROVED_GIT_SHA"
export COURSE_TRANSCRIPT_ENV_FILE=/home/ubuntu/.env
export COURSE_TRANSCRIPT_TARGETARCH=arm64
export COURSE_TRANSCRIPT_DATA_HOST_PATH=/opt/course-transcript-source/data
export COURSE_TRANSCRIPT_LOGS_HOST_PATH=/opt/course-transcript-source/logs
export COURSE_TRANSCRIPT_TMP_HOST_PATH=/opt/course-transcript-source/tmp
export RCLONE_CONFIG_HOST_PATH=/home/ubuntu/.config/rclone/rclone.conf

COMPOSE=(
  sudo -E docker compose
  --project-name course-transcript-source
  --env-file /home/ubuntu/.env
  -f docker-compose.yml
  -f docker-compose.release.yml
)

"${COMPOSE[@]}" --profile web config --quiet
"${COMPOSE[@]}" --profile billing config --quiet

"${COMPOSE[@]}" --profile web config --format json | python3 - "$APPROVED_GIT_SHA" <<'PY'
import json, sys

approved = sys.argv[1]
model = json.load(sys.stdin)
services = model.get('services', {})
required = ('api', 'worker', 'pipeline-worker', 'delivery-worker')
expected_data = '/opt/course-transcript-source/data'

sources = {}
for name in required:
    service = services.get(name)
    if not service:
        raise SystemExit(f'GATE_FAIL: missing service {name}')
    mounts = service.get('volumes', []) or []
    data_mounts = []
    for volume in mounts:
        if isinstance(volume, dict):
            source, target = volume.get('source'), volume.get('target')
            read_only = bool(volume.get('read_only'))
        else:
            parts = str(volume).split(':')
            source, target = parts[0], parts[1] if len(parts) > 1 else ''
            read_only = len(parts) > 2 and 'ro' in parts[2:]
        if target == '/app/data':
            data_mounts.append(source)
        if target in {
            '/run/secrets/rclone.conf',
            '/run/secrets/minimax-api-key',
            '/run/secrets/google-drive-refresh-token',
            '/run/secrets/youtube-owner-refresh-token',
            '/run/secrets/billing-sa.json',
        } and not read_only:
            raise SystemExit(f'GATE_FAIL: sensitive mount not read-only: {name}:{target}')
    if data_mounts != [expected_data]:
        raise SystemExit(f'GATE_FAIL: {name} /app/data={data_mounts!r}')
    sources[name] = data_mounts[0]

for name in ('api', 'worker', 'pipeline-worker', 'delivery-worker', 'frontend'):
    service = services.get(name)
    if not service:
        raise SystemExit(f'GATE_FAIL: missing service {name}')
    image = str(service.get('image') or '')
    if not image.endswith(':' + approved):
        raise SystemExit(f'GATE_FAIL: {name} image is not exact-SHA tagged: {image}')

frontend = services['frontend']
ports = frontend.get('ports', []) or []
serialized = json.dumps(ports)
if '127.0.0.1' not in serialized or '3300' not in serialized:
    raise SystemExit('GATE_FAIL: frontend 127.0.0.1:3300 binding not preserved')

print('shared_app_data_gate=PASS')
print('exact_sha_image_tag_gate=PASS')
print('sensitive_mount_gate=PASS')
print('frontend_bind_gate=PASS')
PY
```

Do not print the rendered environment. The API AI-runtime/account control-plane mounts have their own intentional write policy; this gate only enforces read-only status on dedicated credential mounts listed above.

---

## Phase E — ARM64 build and non-paid validation

```bash
set -euo pipefail
RELEASE_DIR="/opt/course-transcript-releases/${APPROVED_GIT_SHA}"
cd "$RELEASE_DIR"

export COURSE_TRANSCRIPT_RELEASE_TAG="$APPROVED_GIT_SHA"
export COURSE_TRANSCRIPT_ENV_FILE=/home/ubuntu/.env
export COURSE_TRANSCRIPT_TARGETARCH=arm64
export COURSE_TRANSCRIPT_DATA_HOST_PATH=/opt/course-transcript-source/data
export COURSE_TRANSCRIPT_LOGS_HOST_PATH=/opt/course-transcript-source/logs
export COURSE_TRANSCRIPT_TMP_HOST_PATH=/opt/course-transcript-source/tmp
export RCLONE_CONFIG_HOST_PATH=/home/ubuntu/.config/rclone/rclone.conf

python3 -m compileall -q app tests
python3 -m unittest discover -s tests -v

COMPOSE=(
  sudo -E docker compose
  --project-name course-transcript-source
  --env-file /home/ubuntu/.env
  -f docker-compose.yml
  -f docker-compose.release.yml
)

"${COMPOSE[@]}" --profile web build api worker pipeline-worker delivery-worker frontend

"${COMPOSE[@]}" --profile web run --rm --no-deps api \
  python -c "from app.api_hardened import app; assert app.title"

"${COMPOSE[@]}" --profile web run --rm --no-deps pipeline-worker \
  python -c "import app.pipeline.dynamic_worker_production"

"${COMPOSE[@]}" --profile web run --rm --no-deps delivery-worker \
  python -c "import app.jobs.delivery_worker"

for image in \
  "course-transcript-api:${APPROVED_GIT_SHA}" \
  "course-transcript-worker:${APPROVED_GIT_SHA}" \
  "course-transcript-pipeline-worker:${APPROVED_GIT_SHA}" \
  "course-transcript-delivery-worker:${APPROVED_GIT_SHA}" \
  "course-transcript-frontend:${APPROVED_GIT_SHA}"
do
  revision="$(sudo docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
  test "$revision" = "$APPROVED_GIT_SHA" || {
    echo "GATE_FAIL: image revision mismatch $image revision=$revision"
    exit 1
  }
done

echo 'arm64_build_and_revision_gate=PASS'
```

No provider or Drive mutation is permitted in this phase.

---

## Phase F — Controlled service replacement

This phase requires `DEPLOYMENT_WINDOW_APPROVED=yes`. Replace only names explicitly present in `APPROVED_SERVICES`. Do not automatically add services.

Before replacement:

1. compare each target service's current revision with `APPROVED_GIT_SHA`;
2. never replace a service already running a revision newer than the approved SHA;
3. do not replace `delivery-worker` when Phase A found an actionable Drive candidate;
4. preserve any currently required Compose overlays. If the running service labels show overlays beyond `docker-compose.yml` + `docker-compose.release.yml`, stop and report their basenames before replacement unless the same overlays are explicitly included from the new release.

The safe base command is:

```bash
set -euo pipefail
test "${DEPLOYMENT_WINDOW_APPROVED:-no}" = yes
: "${APPROVED_SERVICES:?APPROVED_SERVICES is required}"

RELEASE_DIR="/opt/course-transcript-releases/${APPROVED_GIT_SHA}"
cd "$RELEASE_DIR"

export COURSE_TRANSCRIPT_RELEASE_TAG="$APPROVED_GIT_SHA"
export COURSE_TRANSCRIPT_ENV_FILE=/home/ubuntu/.env
export COURSE_TRANSCRIPT_TARGETARCH=arm64
export COURSE_TRANSCRIPT_DATA_HOST_PATH=/opt/course-transcript-source/data
export COURSE_TRANSCRIPT_LOGS_HOST_PATH=/opt/course-transcript-source/logs
export COURSE_TRANSCRIPT_TMP_HOST_PATH=/opt/course-transcript-source/tmp
export RCLONE_CONFIG_HOST_PATH=/home/ubuntu/.config/rclone/rclone.conf

IFS=',' read -r -a SERVICES <<< "$APPROVED_SERVICES"

sudo -E docker compose \
  --project-name course-transcript-source \
  --env-file /home/ubuntu/.env \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --profile web up -d --no-build "${SERVICES[@]}"
```

If production requires an additional overlay (for example reviewer-specific settings), add that exact release-local overlay to the command **only after it is identified from the running deployment and explicitly preserved**. Never invent or drop an overlay silently.

After replacement, inspect only the approved services and require their OCI revision label to equal the exact approved SHA.

Health checks:

```bash
sudo docker ps --filter 'label=com.docker.compose.project=course-transcript-source' \
  --format '{{.Names}} {{.Status}}'

API_CID="$(sudo docker ps \
  --filter 'label=com.docker.compose.project=course-transcript-source' \
  --filter 'label=com.docker.compose.service=api' -q | head -n 1)"

if [ -n "$API_CID" ]; then
  sudo docker exec "$API_CID" python -c \
    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).status)"
fi

curl --fail --silent --show-error http://127.0.0.1:3300/ >/dev/null
ss -ltn | grep '127.0.0.1:3300'
```

Inspect bounded logs for replaced services only. Do not print environment values.

Do not perform an extra `restart delivery-worker` as a generic persistence test. Restart only services explicitly approved and only when their restart cannot trigger an unapproved external mutation.

Finally rerun the Phase A read-only active/lease query and record per-service image revisions again.

---

## Mandatory stop point

After Phase F, stop. Do not create a batch, authorize cost, call Chirp, Gemini or MiniMax, mutate Drive, publish YouTube captions, or alter Billing.

## Report format

```text
VPS_DEPLOY_GATE
approved_sha: <sha>
origin_main_at_gate: <sha>
source_local_head: <sha>
source_worktree_dirty: YES|NO
release_dir: <path>
release_source: CREATED_FROM_GIT_ARCHIVE|EXISTING_VERIFIED
architecture: <uname -m>
running_revisions_before: <service=sha map>
active_or_leased_jobs_before: 0
drive_delivery_candidate_before: NONE|<job id>
backup_root: <path>
database_sha256: <sha256>
backup_jobs_file_count: <integer>
shared_app_data_gate: PASS
exact_sha_image_tag_gate: PASS
sensitive_mount_gate: PASS
frontend_bind_gate: PASS
api_import: PASS
pipeline_import: PASS
delivery_import: PASS
python_tests: <passed count/result>
arm64_build_and_revision_gate: PASS
approved_services: <list>
replaced_services: <list or NONE>
running_revisions_after: <service=sha map>
api_health: PASS|NOT_REPLACED
frontend_health: PASS
frontend_bind: 127.0.0.1:3300 only
active_or_leased_jobs_after: 0
paid_provider_calls_made: NO
drive_mutations_made: NO
youtube_mutations_made: NO
secrets_printed: NO
warnings: <none or exact warning>
FINAL_STATUS: PASS|FAIL|STOP
next_gate: WAITING_FOR_EXPLICIT_PAID_ACCEPTANCE_APPROVAL
```

`STALE_APPROVAL` is always `FINAL_STATUS=STOP` and requires a fresh exact-SHA approval before any service replacement.
