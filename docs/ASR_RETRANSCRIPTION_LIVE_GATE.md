# ASR Retranscription Live Gate

This runbook is the production acceptance gate for the **single-chunk Chirp retranscription candidate** feature merged in `97b6dcc2473a433744a149c3918c770cb0e80ec7`.

The feature is deliberately **disabled by default**. Passing this gate proves one bounded real-provider candidate can be submitted, recovered after a fresh worker process, compared and rejected without mutating accepted transcript artifacts. Passing the gate does **not** itself authorize permanent production enablement.

## Safety contract

- Run `docs/VPS_DEPLOY_GATE.md` first and require a complete non-paid PASS on the exact approved `main` SHA.
- Keep the normal production API with `ASR_RETRANSCRIPTION_ENABLED=false` during this gate.
- Do not edit `.env` merely to perform the gate.
- Use ephemeral `docker compose run` containers with an explicit `ASR_RETRANSCRIPTION_ENABLED=true` only for the approved candidate creation / worker actions below.
- Test exactly one existing job and one existing Chirp chunk.
- Do not create a new course/job, re-run a whole course, invoke Gemini/MiniMax correction, publish Drive outputs, or change Billing/IAM/quota.
- Do not deliberately change the production job revision or accepted chunk just to manufacture a stale-after-submit condition. That race is covered by repository tests. If staleness occurs naturally, follow the natural-stale checks below.
- Never use the legacy destructive chunk retry path for this gate.
- There is no Apply operation in this gate. The terminal operator decision is **reject / do not adopt**.
- Preserve all provider manifests, candidate artifacts, comparison evidence, events and cost evidence.

## Required inputs

Hermes / the VPS operator must receive all of the following before the paid phase:

```text
APPROVED_GIT_SHA=<exact main SHA, currently 97b6dcc2473a433744a149c3918c770cb0e80ec7>
JOB_ID=<existing completed / awaiting_review / failed job>
CHUNK_INDEX=<one existing suspicious chunk index, zero based>
MAX_RETRANSCRIPTION_COST_USD=<explicit maximum for this one chunk>
PAID_ACCEPTANCE_APPROVED=no
```

The first stages below are provider-free. After the preview reports the exact estimate, the operator must separately provide:

```text
PAID_ACCEPTANCE_APPROVED=yes
APPROVED_JOB_ID=<same JOB_ID>
APPROVED_CHUNK_INDEX=<same CHUNK_INDEX>
APPROVED_MAX_RETRANSCRIPTION_COST_USD=<same or lower explicit limit>
```

Approval for another job, another chunk, another provider, or a whole-course retry is not transferable.

## Phase A — Non-paid deployment prerequisite

Complete `docs/VPS_DEPLOY_GATE.md` first. Stop unless all of these are true:

```text
current_sha == APPROVED_GIT_SHA
architecture == aarch64 / ARM64
active_or_leased_jobs_before == 0
shared_app_data_gate == PASS
python_tests == PASS
arm64_build == PASS
api_health == PASS
paid_provider_calls_made == NO
```

Also render the candidate overlay while the feature is disabled:

```bash
set -euo pipefail
cd /opt/course-transcript

sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web config --quiet

MODEL="$(sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web config --format json)"

MODEL="$MODEL" python - <<'PY'
import json, os
model = json.loads(os.environ['MODEL'])
service = model['services']['retranscription-worker']
assert str(service['environment']['ASR_RETRANSCRIPTION_ENABLED']).lower() == 'false'
assert service['command'] == ['python', '-m', 'app.jobs.retranscription_worker_entry']
print('retranscription_overlay_gate=PASS default_enabled=false')
PY
```

Do not start a persistent retranscription worker yet.

## Phase B — Read-only target and artifact snapshot

Set the approved target values in the operator shell. Do not put secrets here.

```bash
export JOB_ID='<approved job id>'
export CHUNK_INDEX='<approved zero-based chunk index>'
export MAX_RETRANSCRIPTION_COST_USD='<approved maximum>'
export LIVE_GATE_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
```

Create an evidence directory and snapshot accepted chunk hashes before any candidate exists:

```bash
set -euo pipefail
cd /opt/course-transcript
mkdir -p "data/live-gates/asr-retranscription/${LIVE_GATE_STAMP}"

JOB_ID="$JOB_ID" CHUNK_INDEX="$CHUNK_INDEX" LIVE_GATE_STAMP="$LIVE_GATE_STAMP" \
python - <<'PY'
from __future__ import annotations
import hashlib, json, os, sqlite3
from pathlib import Path

root = Path('/opt/course-transcript/data')
job_id = os.environ['JOB_ID']
idx = int(os.environ['CHUNK_INDEX'])
evidence = root / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
chunk = root / 'jobs' / job_id / 'chunks' / f'chunk-{idx:03d}'
if not chunk.is_dir():
    raise SystemExit('GATE_STOP: accepted chunk directory missing')

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

files = {}
for name in ('manifest.json', 'words.json', 'partial-transcript.json'):
    path = chunk / name
    if not path.is_file():
        raise SystemExit(f'GATE_STOP: accepted artifact missing: {name}')
    files[name] = sha(path)

connection = sqlite3.connect(f"file:{root / 'course-transcript.db'}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
try:
    job = connection.execute(
        'SELECT id,status,revision FROM jobs WHERE id=?', (job_id,)
    ).fetchone()
    active = connection.execute(
        """
        SELECT id,status FROM asr_retranscription_candidates
        WHERE status IN ('queued','submitted','processing')
        """
    ).fetchall()
finally:
    connection.close()

if job is None:
    raise SystemExit('GATE_STOP: job not found')
if job['status'] not in {'completed','awaiting_review','failed'}:
    raise SystemExit(f"GATE_STOP: job status not eligible: {job['status']}")
if active:
    raise SystemExit('GATE_STOP: another retranscription candidate is active')

payload = {
    'job_id': job_id,
    'chunk_index': idx,
    'job_status': job['status'],
    'job_revision': int(job['revision']),
    'accepted_sha256_before': files,
}
(evidence / 'accepted-before.json').write_text(
    json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
)
print(f"job_id={job_id} chunk_index={idx} status={job['status']} revision={job['revision']}")
print('accepted_snapshot=PASS')
PY
```

## Phase C — Provider-free quality and cost preview

This phase exercises the production route code inside an ephemeral API container, but it does not create a candidate and cannot call Chirp.

```bash
set -euo pipefail
cd /opt/course-transcript

sudo docker compose --profile web run --rm --no-deps \
  -e COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS=false \
  -e JOB_ID="$JOB_ID" \
  -e CHUNK_INDEX="$CHUNK_INDEX" \
  -e MAX_RETRANSCRIPTION_COST_USD="$MAX_RETRANSCRIPTION_COST_USD" \
  -e LIVE_GATE_STAMP="$LIVE_GATE_STAMP" \
  api python - <<'PY'
from __future__ import annotations
import json, os
from decimal import Decimal
from pathlib import Path
from fastapi.testclient import TestClient

from app.api_hardened import app
from app.jobs.store import JobStore

job_id = os.environ['JOB_ID']
idx = int(os.environ['CHUNK_INDEX'])
limit = Decimal(os.environ['MAX_RETRANSCRIPTION_COST_USD'])
data = Path('/app/data')
store = JobStore(data / 'course-transcript.db')
job = store.get_job(job_id)

with TestClient(app) as client:
    response = client.post(
        f'/api/v1/jobs/{job_id}/retranscription-candidates/preview',
        json={'expected_revision': int(job['revision']), 'chunk_index': idx},
    )
    if response.status_code != 200:
        raise SystemExit(f'GATE_STOP: preview HTTP {response.status_code}: {response.text[:300]}')
    preview = response.json()

estimate = Decimal(str(preview['estimate']['estimated_cost_usd']))
if estimate <= 0:
    raise SystemExit('GATE_STOP: non-positive estimate')
if estimate > limit:
    raise SystemExit(f'GATE_STOP: estimate {estimate} exceeds approved max {limit}')
if preview.get('paid_operation_started') is not False:
    raise SystemExit('GATE_FAIL: preview claimed a paid operation')
if preview.get('existing_candidate') is not None:
    raise SystemExit('GATE_STOP: exact candidate already exists; investigate before paid gate')

safe = {
    'job_id': job_id,
    'job_revision': preview['job_revision'],
    'chunk_index': idx,
    'quality': preview['quality'],
    'recommended_for_retranscription': preview['recommended_for_retranscription'],
    'estimate': preview['estimate'],
    'budget': preview['budget'],
    'paid_operation_started': preview['paid_operation_started'],
}
evidence = data / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
(evidence / 'preview.json').write_text(
    json.dumps(safe, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
)
print(f"preview_estimated_cost_usd={estimate}")
print(f"quality_severity={safe['quality']['severity']}")
print(f"recommended={safe['recommended_for_retranscription']}")
print('paid_provider_calls_made=NO')
PY
```

Stop here when `PAID_ACCEPTANCE_APPROVED` is not exactly `yes` for this same job/chunk/cost limit.

## Phase D — Create exactly one durable candidate

After explicit paid acceptance approval, create the candidate twice through the route code and prove idempotency. Candidate creation itself only writes SQLite; it does not call Chirp.

```bash
set -euo pipefail
cd /opt/course-transcript

test "${PAID_ACCEPTANCE_APPROVED:-no}" = 'yes'

sudo docker compose --profile web run --rm --no-deps \
  -e ASR_RETRANSCRIPTION_ENABLED=true \
  -e COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS=false \
  -e JOB_ID="$JOB_ID" \
  -e CHUNK_INDEX="$CHUNK_INDEX" \
  -e MAX_RETRANSCRIPTION_COST_USD="$MAX_RETRANSCRIPTION_COST_USD" \
  -e LIVE_GATE_STAMP="$LIVE_GATE_STAMP" \
  api python - <<'PY'
from __future__ import annotations
import json, os
from decimal import Decimal
from pathlib import Path
from fastapi.testclient import TestClient

from app.api_hardened import app
from app.jobs.store import JobStore

job_id = os.environ['JOB_ID']
idx = int(os.environ['CHUNK_INDEX'])
limit = Decimal(os.environ['MAX_RETRANSCRIPTION_COST_USD'])
data = Path('/app/data')
store = JobStore(data / 'course-transcript.db')
job = store.get_job(job_id)

with TestClient(app) as client:
    preview_response = client.post(
        f'/api/v1/jobs/{job_id}/retranscription-candidates/preview',
        json={'expected_revision': int(job['revision']), 'chunk_index': idx},
    )
    preview_response.raise_for_status()
    preview = preview_response.json()
    estimate = Decimal(str(preview['estimate']['estimated_cost_usd']))
    if estimate > limit:
        raise SystemExit('GATE_STOP: estimate exceeds approved maximum')
    payload = {
        'expected_revision': int(preview['job_revision']),
        'chunk_index': idx,
        'confirmed_estimated_cost_usd': str(estimate),
        'force': False,
    }
    first = client.post(f'/api/v1/jobs/{job_id}/retranscription-candidates', json=payload)
    second = client.post(f'/api/v1/jobs/{job_id}/retranscription-candidates', json=payload)
    if first.status_code != 201 or second.status_code != 201:
        raise SystemExit(
            f'GATE_FAIL: create responses {first.status_code}/{second.status_code}'
        )
    a, b = first.json(), second.json()

if a['candidate']['id'] != b['candidate']['id']:
    raise SystemExit('GATE_FAIL: duplicate create produced different candidate IDs')
if a['created'] is not True or b['created'] is not False:
    raise SystemExit('GATE_FAIL: idempotent create contract not observed')
if a.get('paid_operation_started') is not False or b.get('paid_operation_started') is not False:
    raise SystemExit('GATE_FAIL: API create unexpectedly reported provider work')

safe = {
    'candidate_id': a['candidate']['id'],
    'job_id': job_id,
    'chunk_index': idx,
    'confirmed_cost_usd': a['candidate']['confirmed_cost_usd'],
    'first_created': a['created'],
    'second_created': b['created'],
    'paid_operation_started': False,
}
evidence = data / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
(evidence / 'candidate-created.json').write_text(
    json.dumps(safe, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
)
print(f"candidate_id={safe['candidate_id']}")
print('idempotent_create=PASS')
print('paid_provider_calls_made=NO')
PY
```

Do not create a second candidate if any assertion fails.

## Phase E — One bounded real Chirp submission

Run one fresh ephemeral worker process. This is the first command in this runbook that may make a paid Chirp provider request.

```bash
set -euo pipefail
cd /opt/course-transcript

test "${PAID_ACCEPTANCE_APPROVED:-no}" = 'yes'

sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web run --rm --no-deps \
  -e ASR_RETRANSCRIPTION_ENABLED=true \
  retranscription-worker \
  python -m app.jobs.retranscription_worker_entry --once
```

Immediately inspect only safe durable state; do not print provider payloads or credentials:

```bash
JOB_ID="$JOB_ID" python - <<'PY'
import sqlite3, os
from pathlib import Path

path = Path('/opt/course-transcript/data/course-transcript.db')
connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
try:
    rows = connection.execute(
        """
        SELECT id,status,submitted_at,operation_name,error_kind,error_safe_message
        FROM asr_retranscription_candidates
        WHERE job_id=? ORDER BY requested_at DESC LIMIT 1
        """,
        (os.environ['JOB_ID'],),
    ).fetchall()
    submit_events = connection.execute(
        """
        SELECT COUNT(*) FROM job_events
        WHERE job_id=? AND event_type='asr_retranscription_candidate_submitted'
        """,
        (os.environ['JOB_ID'],),
    ).fetchone()[0]
finally:
    connection.close()
if len(rows) != 1:
    raise SystemExit('GATE_FAIL: expected one target candidate')
row = rows[0]
print(f"candidate_status={row['status']}")
print(f"submitted_at_recorded={bool(row['submitted_at'])}")
print(f"operation_name_recorded={bool(row['operation_name'])}")
print(f"submit_event_count={submit_events}")
if submit_events != 1:
    raise SystemExit('GATE_FAIL: provider submission event count must be exactly one')
PY
```

If the candidate is `failed`, stop and preserve evidence. Do not retry submission by creating another candidate.

## Phase F — Fresh-process recovery / restart proof

Start a **new** ephemeral worker process for recovery. This proves restart recovery rather than same-process continuation.

```bash
sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web run --rm --no-deps \
  -e ASR_RETRANSCRIPTION_ENABLED=true \
  retranscription-worker \
  python -m app.jobs.retranscription_worker_entry --once
```

If the provider is still pending, wait at least the normal recovery interval before another one-pass recovery attempt. Do not create a new candidate and do not run the Chirp submitter directly.

After every recovery pass, require the submission event count to remain exactly `1`. A retained operation must be recovered, never resubmitted.

Terminal acceptable states for this gate:

- `completed` — normal path; continue to comparison / cleanup checks.
- `stale` — only if the source became stale naturally; preserve `stale-result.json`, require no comparison, and stop without attempting another provider call.

A `failed` candidate is a live-gate failure requiring diagnosis before production enablement.

## Phase G — Result, cleanup and immutability checks

For a normal `completed` candidate, verify:

```bash
JOB_ID="$JOB_ID" CHUNK_INDEX="$CHUNK_INDEX" LIVE_GATE_STAMP="$LIVE_GATE_STAMP" \
python - <<'PY'
from __future__ import annotations
import hashlib, json, os, sqlite3
from pathlib import Path

root = Path('/opt/course-transcript/data')
job_id = os.environ['JOB_ID']
idx = int(os.environ['CHUNK_INDEX'])
evidence = root / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
before = json.loads((evidence / 'accepted-before.json').read_text(encoding='utf-8'))

connection = sqlite3.connect(f'file:{root / "course-transcript.db"}?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
try:
    row = connection.execute(
        """
        SELECT * FROM asr_retranscription_candidates
        WHERE job_id=? ORDER BY requested_at DESC LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    submit_events = connection.execute(
        """
        SELECT COUNT(*) FROM job_events
        WHERE job_id=? AND event_type='asr_retranscription_candidate_submitted'
        """,
        (job_id,),
    ).fetchone()[0]
finally:
    connection.close()

if row is None or row['status'] != 'completed':
    raise SystemExit(f"GATE_STOP: candidate is not completed: {None if row is None else row['status']}")
if submit_events != 1:
    raise SystemExit('GATE_FAIL: more than one provider submission event')

candidate = root / 'jobs' / job_id / row['candidate_relpath']
comparison = json.loads((candidate / 'comparison.json').read_text(encoding='utf-8'))
if comparison.get('auto_apply') is not False:
    raise SystemExit('GATE_FAIL: comparison auto_apply must be false')
if comparison.get('decision') != 'operator_review_required':
    raise SystemExit('GATE_FAIL: unexpected comparison decision')

manifest = json.loads(
    (candidate / 'chunks' / f'chunk-{idx:03d}' / 'manifest.json').read_text(encoding='utf-8')
)
cleanup = manifest.get('gcs_cleanup') or {}
if cleanup.get('status') != 'completed':
    raise SystemExit(f"GATE_STOP: GCS cleanup is not completed: {cleanup.get('status')}")

accepted = root / 'jobs' / job_id / 'chunks' / f'chunk-{idx:03d}'
def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

after = {
    name: sha(accepted / name)
    for name in ('manifest.json', 'words.json', 'partial-transcript.json')
}
if after != before['accepted_sha256_before']:
    raise SystemExit('GATE_FAIL: accepted chunk artifacts changed')

result = {
    'candidate_id': row['id'],
    'candidate_status': row['status'],
    'submit_event_count': submit_events,
    'comparison_auto_apply': comparison['auto_apply'],
    'comparison_decision': comparison['decision'],
    'gcs_cleanup_status': cleanup['status'],
    'accepted_sha256_after': after,
    'accepted_artifacts_unchanged': True,
}
(evidence / 'completed-check.json').write_text(
    json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
)
print('candidate_recovery=PASS')
print('gcs_cleanup=PASS')
print('accepted_artifacts_unchanged=PASS')
print('submit_event_count=1')
PY
```

### Natural stale path

Do **not** deliberately create this state. If the candidate becomes stale naturally after submission, require all of the following instead:

- `stale-result.json` exists;
- `provider_result_preserved=true`;
- `accepted_artifacts_mutated=false`;
- `comparison_created=false`;
- no `comparison.json` is present;
- submission event count remains exactly `1`;
- accepted chunk hashes remain unchanged.

Then stop. A naturally stale candidate is evidence that the crash/stale protection worked, but it is not a successful quality comparison candidate.

## Phase H — Explicit reject; never Apply

For a normal completed candidate, reject it through the same production route code in an ephemeral API container. The job revision must still match; if it changed, stop instead of forcing a decision.

```bash
sudo docker compose --profile web run --rm --no-deps \
  -e COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS=false \
  -e JOB_ID="$JOB_ID" \
  api python - <<'PY'
from __future__ import annotations
import os
from pathlib import Path
from fastapi.testclient import TestClient
from app.api_hardened import app
from app.jobs.store import JobStore
from app.jobs.retranscription_candidates import RetranscriptionCandidateStore

job_id = os.environ['JOB_ID']
data = Path('/app/data')
store = JobStore(data / 'course-transcript.db')
candidates = RetranscriptionCandidateStore(store)
rows = candidates.list_for_job(job_id)
if not rows or rows[0]['status'] != 'completed':
    raise SystemExit('GATE_STOP: latest candidate is not completed')
row = rows[0]
job = store.get_job(job_id)

with TestClient(app) as client:
    response = client.post(
        f"/api/v1/jobs/{job_id}/retranscription-candidates/{row['id']}/reject",
        json={'expected_revision': int(job['revision'])},
    )
if response.status_code != 200:
    raise SystemExit(f'GATE_FAIL: reject HTTP {response.status_code}: {response.text[:300]}')
payload = response.json()
if payload['candidate']['status'] != 'rejected':
    raise SystemExit('GATE_FAIL: candidate did not become rejected')
if payload.get('accepted_artifacts_mutated') is not False:
    raise SystemExit('GATE_FAIL: reject mutation contract violated')
print('candidate_reject=PASS')
print('accepted_artifacts_mutated=false')
PY
```

Re-run the accepted chunk hash check from Phase G after rejection. It must remain byte-identical.

## Mandatory shutdown state

At the end of the gate:

- do not start `retranscription-worker` as a persistent production service;
- keep normal API `ASR_RETRANSCRIPTION_ENABLED=false`;
- do not add the retranscription overlay to the normal deployment command yet;
- leave the candidate as `rejected` (or naturally `stale`) with all evidence retained;
- do not delete local candidate evidence or manually delete GCS objects outside the recorded recovery path.

A separate reviewed production-enable change may set the feature flag / deployment topology only after this gate is signed off.

## Stop gates

Stop immediately and do not broaden the test when any of these occur:

- target job/chunk differs from the approved scope;
- estimate exceeds `MAX_RETRANSCRIPTION_COST_USD`;
- another active/leased paid job or active retranscription candidate exists;
- preview reports an existing exact candidate unexpectedly;
- duplicate create returns different candidate IDs;
- provider submission event count exceeds `1`;
- candidate enters `failed`;
- accepted chunk hash changes;
- comparison reports `auto_apply=true`;
- GCS cleanup remains `pending` / `disabled` / missing after successful recovery;
- credential/auth/quota/provider-model error occurs;
- any command would require printing or replacing credentials, changing IAM/quota/Billing, or deleting evidence.

Do not respond to a stop gate by creating another candidate or increasing the cost scope.

## Required report

Hermes / the VPS operator returns this exact summary without provider payloads or secrets:

```text
ASR_RETRANSCRIPTION_LIVE_GATE
approved_sha: <sha>
job_id: <id>
chunk_index: <zero-based index>
quality_severity: <severity>
preview_estimated_cost_usd: <amount>
approved_max_cost_usd: <amount>
preview_paid_provider_calls: 0
candidate_id: <id>
idempotent_create: PASS
first_real_submit: PASS
submit_event_count: 1
restart_recovery: PASS
terminal_candidate_status: rejected | stale
gcs_cleanup: PASS | <natural-stale evidence status>
comparison_auto_apply: false | n/a-natural-stale
accepted_artifacts_unchanged: PASS
persistent_worker_enabled: NO
normal_api_paid_gate_enabled: NO
drive_mutations_made: NO
other_provider_calls_made: NO
secrets_printed: NO
warnings: <none or exact warning>
next_gate: REVIEW_RESULTS_BEFORE_ANY_PRODUCTION_ENABLEMENT
```
