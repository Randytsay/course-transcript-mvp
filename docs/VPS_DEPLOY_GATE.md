# VPS Deploy Gate for Hermes

This document is the operating contract between the repository maintainer and the Hermes agent running on the Oracle VPS.

Hermes may execute the non-paid deployment gate below after receiving an exact approved Git SHA. Hermes must stop before any real Chirp, Gemini, or Drive mutation test unless the user explicitly approves that exact test.

## Operating rules

Hermes must:

- work only inside `/opt/course-transcript` except for timestamped backups;
- never print or copy service-account JSON, rclone configuration, OAuth tokens, Cloudflare tunnel tokens, or secret environment values;
- never use `git reset --hard`, `git clean -fd`, `rm -rf` on the project/data directories, or any command that deletes untracked VPS data;
- never change Cloudflare Access, DNS, tunnel configuration, IAM, firewall, Billing, or GCP quotas;
- never authorize a paid job or infer approval from an existing job/page/file;
- never re-submit a retained Chirp operation merely because its output is pending;
- stop immediately if an active paid job, non-expired lease, failed backup, shared-volume mismatch, secret-mount mismatch, or unexplained service state is found.

## Inputs Hermes must receive

Before starting, Hermes must be given:

```text
APPROVED_GIT_SHA=<exact main commit SHA>
DEPLOYMENT_WINDOW_APPROVED=yes
PAID_ACCEPTANCE_APPROVED=no
```

Unless `PAID_ACCEPTANCE_APPROVED=yes` is separately supplied with the exact source and cost scope, Hermes performs only non-paid deployment and validation.

## Phase A — Read-only inventory

Run from the VPS:

```bash
set -euo pipefail
cd /opt/course-transcript

printf 'current_sha=' && git rev-parse HEAD
printf 'branch=' && git branch --show-current
printf 'architecture=' && uname -m
printf 'docker=' && docker --version
printf 'compose=' && docker compose version
printf 'free_space=' && df -h /opt/course-transcript | tail -n 1
```

Inspect services without exposing environment values:

```bash
sudo docker compose \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web --profile tunnel ps
```

Inspect the database read-only. This query must not modify SQLite:

```bash
python - <<'PY'
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

path = Path('/opt/course-transcript/data/course-transcript.db')
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
        'job',
        row['id'],
        f"status={row['status']}",
        f"stage={row['active_stage']}",
        f"locked_by={row['locked_by']}",
        f"lease_expires_at={row['lease_expires_at']}",
    )

if rows:
    raise SystemExit('GATE_STOP: active or leased jobs require human review')
PY
```

Do not proceed when this phase reports any active or leased job.

## Phase B — Verified backup

Create a backup outside the repository worktree:

```bash
set -euo pipefail
cd /opt/course-transcript
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="/opt/course-transcript-backups/${STAMP}"
sudo mkdir -p "${BACKUP_ROOT}"
sudo chown "$(id -u):$(id -g)" "${BACKUP_ROOT}"

cp -a data/course-transcript.db "${BACKUP_ROOT}/course-transcript.db"
cp -a data/jobs "${BACKUP_ROOT}/jobs"

test -s "${BACKUP_ROOT}/course-transcript.db"
test -d "${BACKUP_ROOT}/jobs"

sha256sum "${BACKUP_ROOT}/course-transcript.db" \
  > "${BACKUP_ROOT}/course-transcript.db.sha256"
find "${BACKUP_ROOT}/jobs" -type f | wc -l \
  > "${BACKUP_ROOT}/jobs-file-count.txt"

echo "backup_root=${BACKUP_ROOT}"
cat "${BACKUP_ROOT}/course-transcript.db.sha256"
cat "${BACKUP_ROOT}/jobs-file-count.txt"
```

Hermes may report the backup path, database checksum, and job-file count. It must not print artifact contents.

## Phase C — Safe source update

Fetch and verify the exact approved commit. Do not discard local/untracked data.

```bash
set -euo pipefail
cd /opt/course-transcript

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo 'GATE_STOP: tracked working tree has local changes'
  git status --short
  exit 1
fi

git fetch --prune origin main
git checkout main
git merge --ff-only "${APPROVED_GIT_SHA}"

test "$(git rev-parse HEAD)" = "${APPROVED_GIT_SHA}"
echo "deployed_source_sha=$(git rev-parse HEAD)"
```

If `main` cannot fast-forward to the approved SHA, stop and report. Do not force, reset, or delete.

## Phase D — Compose and shared-volume gate

Render the effective Compose model without printing secrets:

```bash
set -euo pipefail
cd /opt/course-transcript

sudo docker compose \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web --profile tunnel config --quiet

python - <<'PY'
from __future__ import annotations

import subprocess
import yaml

command = [
    'sudo', 'docker', 'compose',
    '-f', 'docker-compose.yml',
    '-f', 'docker-compose.cloudflare.yml',
    '--profile', 'web',
    '--profile', 'tunnel',
    'config',
]
model = yaml.safe_load(subprocess.check_output(command, text=True))
services = model.get('services', {})
required = ('api', 'pipeline-worker', 'delivery-worker')
sources = {}

for name in required:
    service = services.get(name)
    if not service:
        raise SystemExit(f'GATE_FAIL: missing service {name}')
    matches = []
    for volume in service.get('volumes', []):
        if isinstance(volume, str):
            source, target, *_ = volume.split(':')
        else:
            source = volume.get('source')
            target = volume.get('target')
        if target == '/app/data':
            matches.append(source)
    if len(matches) != 1:
        raise SystemExit(f'GATE_FAIL: {name} must have exactly one /app/data mount')
    sources[name] = matches[0]

print('app_data_sources=', sources)
if len(set(sources.values())) != 1:
    raise SystemExit('GATE_FAIL: /app/data sources differ; fcntl lock is unsafe')
print('shared_app_data_gate=PASS')
PY
```

If PyYAML is unavailable on the host, run the equivalent inspection inside the API image or report `GATE_STOP: PyYAML unavailable`; do not skip the shared-volume verification.

Also verify the rendered mounts for service-account and rclone files remain read-only. Report only mount paths and read-only flags, never file contents.

## Phase E — ARM64 build and non-paid tests

```bash
set -euo pipefail
cd /opt/course-transcript

python -m compileall -q app tests
python -m unittest discover -s tests -v

sudo docker compose --profile web build \
  api worker pipeline-worker delivery-worker frontend

sudo docker compose --profile web run --rm --no-deps api \
  python -c "from app.api_hardened import app; assert app.title"

sudo docker compose --profile web run --rm --no-deps pipeline-worker \
  python -c "import app.pipeline.dynamic_worker_production"

sudo docker compose --profile web run --rm --no-deps delivery-worker \
  python -c "import app.jobs.delivery_worker"
```

No provider or Drive mutation is permitted in this phase.

## Phase F — Controlled restart

```bash
set -euo pipefail
cd /opt/course-transcript

sudo docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web --profile tunnel up -d --build

sudo docker compose \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web --profile tunnel ps
```

Verify API health from inside the Docker network and frontend binding from the host:

```bash
sudo docker compose --profile web exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).status)"

curl --fail --silent --show-error http://127.0.0.1:3300/ >/dev/null
ss -ltn | grep '127.0.0.1:3300'
```

Inspect bounded logs without printing environment or secret files:

```bash
sudo docker compose --profile web logs --tail=100 \
  api worker pipeline-worker delivery-worker frontend
```

Restart-persistence check:

```bash
sudo docker compose --profile web restart pipeline-worker delivery-worker
sleep 5
sudo docker compose --profile web ps pipeline-worker delivery-worker
```

Then rerun the read-only active-job query from Phase A and verify no duplicate paid submission or unexpected Drive event appeared.

## Mandatory stop point

After Phase F, Hermes must stop and return the report below. It must not create a batch, authorize cost, call Chirp, call Gemini, upload/rename Drive files, or alter Billing.

## Report format Hermes must return

```text
VPS_DEPLOY_GATE
approved_sha: <sha>
previous_sha: <sha>
current_sha: <sha>
architecture: <uname -m>
active_or_leased_jobs_before: 0
backup_root: <path>
database_sha256: <sha256>
backup_jobs_file_count: <integer>
shared_app_data_gate: PASS
api_import: PASS
pipeline_import: PASS
delivery_import: PASS
python_tests: <passed count / result>
compose_web_config: PASS
compose_tunnel_config: PASS
arm64_build: PASS
api_health: PASS
frontend_health: PASS
frontend_bind: 127.0.0.1:3300 only
services: <concise status>
restart_persistence: PASS
active_or_leased_jobs_after: 0
paid_provider_calls_made: NO
drive_mutations_made: NO
secrets_printed: NO
warnings: <none or exact warning>
next_gate: WAITING_FOR_EXPLICIT_PAID_ACCEPTANCE_APPROVAL
```

## Optional paid acceptance — only after a new explicit approval

A separate approval must name:

- the exact Drive source file;
- expected duration;
- estimated Chirp and Gemini cost;
- output formats;
- whether same-name Drive publication is part of the test;
- the disposable Drive folder for any mutation test.

Hermes must then execute one test at a time and return evidence before proceeding to the next. Failure at any step stops the sequence. Real provider validation must never be combined with an unrelated production batch.
