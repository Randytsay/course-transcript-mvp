# ASR Retranscription Live Gate

This is the production acceptance gate for the **single-chunk Chirp retranscription candidate** feature merged in `97b6dcc2473a433744a149c3918c770cb0e80ec7`.

The feature remains disabled by default. This gate proves one bounded, explicitly approved candidate can be created idempotently, submitted once, recovered by a fresh worker process, compared, cleaned up and rejected without mutating accepted transcript artifacts.

Passing this gate does **not** enable the feature permanently.

## Invariants

- Run `docs/VPS_DEPLOY_GATE.md` first on the exact approved `main` SHA.
- Keep the normal production API at `ASR_RETRANSCRIPTION_ENABLED=false`.
- Do not edit `.env` for this test.
- Use ephemeral `docker compose run` containers with an explicit one-command override only where stated.
- Test one existing job and one existing suspicious Chirp chunk.
- Require `processing_strategy=DYNAMIC_BATCHING` so a retained operation / restart-recovery path is actually exercised.
- Do not create a new course, rerun a whole course, invoke Gemini/MiniMax, publish Drive outputs, or change Billing/IAM/quota.
- Never use the destructive legacy chunk-retry path.
- Never deliberately change a real job revision or accepted chunk to manufacture staleness. The stale-after-submit race is covered by repository tests.
- There is no Apply step. A successful live candidate ends as **rejected**.
- Preserve candidate manifests, operation evidence, comparison, events and cost evidence.

## Inputs

Before provider-free preview:

```text
APPROVED_GIT_SHA=<exact deployed main SHA>
JOB_ID=<existing completed / awaiting_review / failed job>
CHUNK_INDEX=<zero-based suspicious chunk index>
MAX_RETRANSCRIPTION_COST_USD=<hard ceiling for this one chunk>
PAID_ACCEPTANCE_APPROVED=no
```

After preview, paid execution additionally requires explicit approval for the same target and exact estimate:

```text
PAID_ACCEPTANCE_APPROVED=yes
APPROVED_JOB_ID=<same JOB_ID>
APPROVED_CHUNK_INDEX=<same CHUNK_INDEX>
APPROVED_ESTIMATE_USD=<exact preview estimate>
```

Approval is not transferable to another job/chunk/provider or a second candidate.

---

## Phase A — Non-paid deployment gate

Complete `docs/VPS_DEPLOY_GATE.md`. Require:

```text
current_sha == APPROVED_GIT_SHA
architecture == ARM64/aarch64
active_or_leased_jobs_before == 0
shared_app_data_gate == PASS
python_tests == PASS
arm64_build == PASS
api_health == PASS
paid_provider_calls_made == NO
```

Verify the retranscription overlay renders and remains disabled by default without printing the rendered environment:

```bash
set -euo pipefail
cd /opt/course-transcript

sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web config --quiet

sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web config --format json \
| python -c "import json,sys; s=json.load(sys.stdin)['services']['retranscription-worker']; assert str(s['environment']['ASR_RETRANSCRIPTION_ENABLED']).lower()=='false'; assert s['command']==['python','-m','app.jobs.retranscription_worker_entry']; print('retranscription_overlay_gate=PASS default_enabled=false')"
```

Do not start the worker persistently.

---

## Phase B — Provider-free schema initialization

The candidate table is initialized lazily. Create only the local SQLite schema before any read-only candidate query:

```bash
set -euo pipefail
cd /opt/course-transcript

sudo docker compose --profile web run --rm --no-deps -T api python - <<'PY'
from pathlib import Path
from app.jobs.store import JobStore
from app.jobs.retranscription_candidates import RetranscriptionCandidateStore

store = JobStore(Path('/app/data/course-transcript.db'))
RetranscriptionCandidateStore(store)
print('candidate_schema=READY provider_calls=0')
PY
```

This step does not call Chirp.

---

## Phase C — Target snapshot and active-work gate

```bash
export JOB_ID='<approved job id>'
export CHUNK_INDEX='<approved zero-based chunk index>'
export MAX_RETRANSCRIPTION_COST_USD='<approved hard ceiling>'
export LIVE_GATE_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "/opt/course-transcript/data/live-gates/asr-retranscription/${LIVE_GATE_STAMP}"
```

Snapshot the accepted artifacts and ensure no retranscription candidate is active:

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
chunk = root / 'jobs' / job_id / 'chunks' / f'chunk-{idx:03d}'

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

if not chunk.is_dir():
    raise SystemExit('GATE_STOP: accepted chunk missing')
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
        'SELECT id,status,revision,processing_strategy FROM jobs WHERE id=?', (job_id,)
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
    raise SystemExit(f"GATE_STOP: ineligible job status {job['status']}")
if job['processing_strategy'] != 'DYNAMIC_BATCHING':
    raise SystemExit('GATE_STOP: live restart gate requires DYNAMIC_BATCHING target')
if active:
    raise SystemExit('GATE_STOP: another retranscription candidate is active')

payload = {
    'job_id': job_id,
    'chunk_index': idx,
    'job_status': job['status'],
    'job_revision': int(job['revision']),
    'processing_strategy': job['processing_strategy'],
    'accepted_sha256_before': files,
}
(evidence / 'accepted-before.json').write_text(
    json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
)
print(f"target=PASS job={job_id} chunk={idx} revision={job['revision']}")
print('accepted_snapshot=PASS active_retranscription_candidates=0')
PY
```

---

## Phase D — Provider-free quality and cost preview

Exercise the production API route inside an ephemeral container. The paid gate stays false.

```bash
sudo docker compose --profile web run --rm --no-deps -T \
  -e ASR_RETRANSCRIPTION_ENABLED=false \
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
job = JobStore(data / 'course-transcript.db').get_job(job_id)

with TestClient(app) as client:
    response = client.post(
        f'/api/v1/jobs/{job_id}/retranscription-candidates/preview',
        json={'expected_revision': int(job['revision']), 'chunk_index': idx},
    )
if response.status_code != 200:
    raise SystemExit(f'GATE_STOP: preview HTTP {response.status_code}: {response.text[:300]}')
preview = response.json()
estimate = Decimal(str(preview['estimate']['estimated_cost_usd']))

if preview.get('paid_operation_started') is not False:
    raise SystemExit('GATE_FAIL: preview reported provider work')
if preview.get('existing_candidate') is not None:
    raise SystemExit('GATE_STOP: exact candidate already exists')
if not preview.get('recommended_for_retranscription'):
    raise SystemExit('GATE_STOP: selected chunk is not medium/high severity')
if estimate <= 0 or estimate > limit:
    raise SystemExit(f'GATE_STOP: estimate {estimate} outside approved ceiling {limit}')
if preview['estimate']['processing_strategy'] != 'DYNAMIC_BATCHING':
    raise SystemExit('GATE_STOP: preview strategy is not DYNAMIC_BATCHING')

safe = {
    'job_id': job_id,
    'job_revision': preview['job_revision'],
    'chunk_index': idx,
    'quality': preview['quality'],
    'recommended_for_retranscription': True,
    'estimate': preview['estimate'],
    'budget': preview['budget'],
    'paid_operation_started': False,
}
evidence = data / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
(evidence / 'preview.json').write_text(
    json.dumps(safe, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
)
print(f"quality_severity={safe['quality']['severity']}")
print(f"preview_estimated_cost_usd={estimate}")
print('paid_provider_calls_made=NO')
PY
```

**Mandatory stop point:** return the exact preview estimate and wait for `PAID_ACCEPTANCE_APPROVED=yes` plus `APPROVED_ESTIMATE_USD=<exact estimate>` for this same job/chunk.

---

## Phase E — Create one candidate and prove idempotency

Candidate creation writes SQLite only; it does not call Chirp.

```bash
test "${PAID_ACCEPTANCE_APPROVED:-no}" = 'yes'
test "${APPROVED_JOB_ID:-}" = "$JOB_ID"
test "${APPROVED_CHUNK_INDEX:-}" = "$CHUNK_INDEX"

sudo docker compose --profile web run --rm --no-deps -T \
  -e ASR_RETRANSCRIPTION_ENABLED=true \
  -e COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS=false \
  -e JOB_ID="$JOB_ID" \
  -e CHUNK_INDEX="$CHUNK_INDEX" \
  -e APPROVED_ESTIMATE_USD="$APPROVED_ESTIMATE_USD" \
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
approved = Decimal(os.environ['APPROVED_ESTIMATE_USD'])
data = Path('/app/data')
job = JobStore(data / 'course-transcript.db').get_job(job_id)

with TestClient(app) as client:
    pr = client.post(
        f'/api/v1/jobs/{job_id}/retranscription-candidates/preview',
        json={'expected_revision': int(job['revision']), 'chunk_index': idx},
    )
    pr.raise_for_status()
    preview = pr.json()
    estimate = Decimal(str(preview['estimate']['estimated_cost_usd']))
    if estimate != approved:
        raise SystemExit(f'GATE_STOP: estimate changed {estimate} != approved {approved}')
    body = {
        'expected_revision': int(preview['job_revision']),
        'chunk_index': idx,
        'confirmed_estimated_cost_usd': str(estimate),
        'force': False,
    }
    first = client.post(f'/api/v1/jobs/{job_id}/retranscription-candidates', json=body)
    second = client.post(f'/api/v1/jobs/{job_id}/retranscription-candidates', json=body)

if first.status_code != 201 or second.status_code != 201:
    raise SystemExit(f'GATE_FAIL: create HTTP {first.status_code}/{second.status_code}')
a, b = first.json(), second.json()
if a['candidate']['id'] != b['candidate']['id']:
    raise SystemExit('GATE_FAIL: duplicate create produced two candidate IDs')
if a['created'] is not True or b['created'] is not False:
    raise SystemExit('GATE_FAIL: idempotent create contract failed')
if a.get('paid_operation_started') is not False or b.get('paid_operation_started') is not False:
    raise SystemExit('GATE_FAIL: API creation unexpectedly reported provider work')

safe = {
    'candidate_id': a['candidate']['id'],
    'job_id': job_id,
    'chunk_index': idx,
    'confirmed_cost_usd': a['candidate']['confirmed_cost_usd'],
    'first_created': True,
    'second_created': False,
}
evidence = data / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
(evidence / 'candidate-created.json').write_text(
    json.dumps(safe, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
)
print(f"candidate_id={safe['candidate_id']}")
print('idempotent_create=PASS provider_calls=0')
PY
```

---

## Phase F — One real Chirp submission

This is the first command allowed to make a paid provider call.

```bash
sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web run --rm --no-deps \
  -e ASR_RETRANSCRIPTION_ENABLED=true \
  retranscription-worker \
  python -m app.jobs.retranscription_worker_entry --once
```

Immediately verify the **target candidate** has exactly one submit event. Do not count historical candidates for the same job.

```bash
JOB_ID="$JOB_ID" LIVE_GATE_STAMP="$LIVE_GATE_STAMP" python - <<'PY'
import json, os, sqlite3
from pathlib import Path
root = Path('/opt/course-transcript/data')
evidence = root / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
candidate_id = json.loads((evidence / 'candidate-created.json').read_text())['candidate_id']
connection = sqlite3.connect(f"file:{root / 'course-transcript.db'}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
try:
    row = connection.execute(
        'SELECT status,submitted_at,operation_name,error_kind FROM asr_retranscription_candidates WHERE id=?',
        (candidate_id,),
    ).fetchone()
    events = connection.execute(
        "SELECT payload_json FROM job_events WHERE job_id=? AND event_type='asr_retranscription_candidate_submitted'",
        (os.environ['JOB_ID'],),
    ).fetchall()
finally:
    connection.close()
count = 0
for event in events:
    try:
        if json.loads(event['payload_json']).get('candidate_id') == candidate_id:
            count += 1
    except Exception:
        pass
if row is None:
    raise SystemExit('GATE_FAIL: candidate disappeared')
print(f"candidate_status={row['status']}")
print(f"submitted_at_recorded={bool(row['submitted_at'])}")
print(f"operation_name_recorded={bool(row['operation_name'])}")
print(f"target_submit_event_count={count}")
if count != 1:
    raise SystemExit('GATE_FAIL: target submit event count must equal one')
if row['status'] == 'failed':
    raise SystemExit('GATE_STOP: candidate failed after first real submission')
PY
```

If the first pass already becomes `completed`, stop with `GATE_INCONCLUSIVE: provider completed before restart recovery could be demonstrated`. Do **not** create a second candidate merely to force a pending operation.

---

## Phase G — Fresh-process restart recovery

For `submitted` / `processing`, start a new ephemeral worker process:

```bash
sudo docker compose \
  -f docker-compose.yml \
  -f docker-compose.retranscription.yml \
  --profile web run --rm --no-deps \
  -e ASR_RETRANSCRIPTION_ENABLED=true \
  retranscription-worker \
  python -m app.jobs.retranscription_worker_entry --once
```

If still pending, do not poll faster than the normal Chirp recovery cadence. Every pass must keep the target submit event count at exactly `1`; never create another candidate or run the submitter directly.

Terminal states:

- `completed` — continue to Phase H.
- `stale` — only when staleness happened naturally; verify `stale-result.json`, no comparison, unchanged accepted hashes, then stop.
- `failed` — live gate FAIL; preserve evidence and diagnose before any production enablement.

Do not deliberately alter job revision to exercise the stale branch on production data.

---

## Phase H — Cleanup, comparison and immutability

For a `completed` candidate, verify all of the following:

- candidate `comparison.json` exists;
- `auto_apply=false`;
- `decision=operator_review_required`;
- candidate chunk manifest has `gcs_cleanup.status=completed`;
- target submit event count is exactly `1`;
- accepted `manifest.json`, `words.json`, `partial-transcript.json` hashes are byte-identical to Phase C.

Use this check:

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
created = json.loads((evidence / 'candidate-created.json').read_text())
before = json.loads((evidence / 'accepted-before.json').read_text())
candidate_id = created['candidate_id']

connection = sqlite3.connect(f"file:{root / 'course-transcript.db'}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
try:
    row = connection.execute('SELECT * FROM asr_retranscription_candidates WHERE id=?', (candidate_id,)).fetchone()
    events = connection.execute(
        "SELECT payload_json FROM job_events WHERE job_id=? AND event_type='asr_retranscription_candidate_submitted'",
        (job_id,),
    ).fetchall()
finally:
    connection.close()
if row is None or row['status'] != 'completed':
    raise SystemExit(f"GATE_STOP: target is not completed: {None if row is None else row['status']}")

submit_count = 0
for event in events:
    try:
        if json.loads(event['payload_json']).get('candidate_id') == candidate_id:
            submit_count += 1
    except Exception:
        pass
if submit_count != 1:
    raise SystemExit('GATE_FAIL: duplicate provider submission evidence')

candidate = root / 'jobs' / job_id / row['candidate_relpath']
comparison = json.loads((candidate / 'comparison.json').read_text())
if comparison.get('auto_apply') is not False or comparison.get('decision') != 'operator_review_required':
    raise SystemExit('GATE_FAIL: comparison safety contract failed')
manifest = json.loads((candidate / 'chunks' / f'chunk-{idx:03d}' / 'manifest.json').read_text())
cleanup = (manifest.get('gcs_cleanup') or {}).get('status')
if cleanup != 'completed':
    raise SystemExit(f'GATE_STOP: GCS cleanup status is {cleanup!r}')

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()
accepted = root / 'jobs' / job_id / 'chunks' / f'chunk-{idx:03d}'
after = {name: sha(accepted / name) for name in ('manifest.json','words.json','partial-transcript.json')}
if after != before['accepted_sha256_before']:
    raise SystemExit('GATE_FAIL: accepted chunk changed')

result = {
    'candidate_id': candidate_id,
    'submit_event_count': submit_count,
    'gcs_cleanup_status': cleanup,
    'comparison_auto_apply': comparison['auto_apply'],
    'comparison_decision': comparison['decision'],
    'accepted_artifacts_unchanged': True,
}
(evidence / 'completed-check.json').write_text(json.dumps(result, indent=2) + '\n')
print('restart_recovery=PASS')
print('target_submit_event_count=1')
print('gcs_cleanup=PASS')
print('comparison_auto_apply=false')
print('accepted_artifacts_unchanged=PASS')
PY
```

A cleanup status of `pending`, `disabled` or missing is a stop gate. Do not manually delete cloud evidence to make the test green.

### Natural stale path

If staleness occurs naturally after submission, require:

- candidate status `stale`;
- `stale-result.json` exists;
- `provider_result_preserved=true`;
- `accepted_artifacts_mutated=false`;
- `comparison_created=false`;
- no candidate `comparison.json`;
- target submit event count remains exactly `1`;
- accepted hashes remain unchanged.

Then stop. Natural stale recovery proves safety but does not produce a comparison candidate.

---

## Phase I — Reject; never Apply

For a normal `completed` candidate, reject it through the production route code with the current job revision:

```bash
sudo docker compose --profile web run --rm --no-deps -T \
  -e ASR_RETRANSCRIPTION_ENABLED=false \
  -e COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS=false \
  -e JOB_ID="$JOB_ID" \
  -e LIVE_GATE_STAMP="$LIVE_GATE_STAMP" \
  api python - <<'PY'
from __future__ import annotations
import json, os
from pathlib import Path
from fastapi.testclient import TestClient
from app.api_hardened import app
from app.jobs.store import JobStore
from app.jobs.retranscription_candidates import RetranscriptionCandidateStore

data = Path('/app/data')
job_id = os.environ['JOB_ID']
evidence = data / 'live-gates' / 'asr-retranscription' / os.environ['LIVE_GATE_STAMP']
candidate_id = json.loads((evidence / 'candidate-created.json').read_text())['candidate_id']
store = JobStore(data / 'course-transcript.db')
row = RetranscriptionCandidateStore(store).get(candidate_id)
if row['status'] != 'completed':
    raise SystemExit('GATE_STOP: candidate is no longer completed')
job = store.get_job(job_id)
with TestClient(app) as client:
    response = client.post(
        f'/api/v1/jobs/{job_id}/retranscription-candidates/{candidate_id}/reject',
        json={'expected_revision': int(job['revision'])},
    )
if response.status_code != 200:
    raise SystemExit(f'GATE_FAIL: reject HTTP {response.status_code}: {response.text[:300]}')
payload = response.json()
if payload['candidate']['status'] != 'rejected' or payload.get('accepted_artifacts_mutated') is not False:
    raise SystemExit('GATE_FAIL: reject safety contract failed')
print('candidate_reject=PASS accepted_artifacts_mutated=false')
PY
```

Re-run the accepted hash comparison from Phase H. It must still pass.

---

## Mandatory final state

- normal API feature flag: `false`;
- persistent retranscription worker: **not started**;
- retranscription overlay: **not added** to normal production deployment yet;
- test candidate: `rejected` or naturally `stale`;
- no Drive mutation;
- no Gemini/MiniMax call;
- evidence retained.

Production enablement requires a separate reviewed change after this report is accepted.

## Stop gates

Stop without broadening the test if any of these occur:

- target or estimate differs from approved scope;
- another active/leased paid job or retranscription candidate exists;
- selected chunk is not medium/high severity;
- duplicate create yields two IDs;
- target submit event count exceeds one;
- candidate fails;
- accepted artifact hash changes;
- comparison ever reports auto-apply;
- GCS cleanup is not completed;
- auth/quota/provider-model error occurs;
- a command would require credential output/replacement, Billing/IAM/quota changes, evidence deletion, or a second paid candidate.

## Required report

```text
ASR_RETRANSCRIPTION_LIVE_GATE
approved_sha: <sha>
job_id: <id>
chunk_index: <index>
quality_severity: <severity>
preview_estimated_cost_usd: <exact amount>
approved_estimate_usd: <same amount>
idempotent_create: PASS
first_real_submit: PASS
target_submit_event_count: 1
restart_recovery: PASS
terminal_candidate_status: rejected | stale
gcs_cleanup: PASS | <natural stale evidence>
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

## Related gates

- Non-paid deployment: `docs/VPS_DEPLOY_GATE.md`
- MiniMax M3 real-provider validation: `docs/MINIMAX_M3_WINDOWED_CORRECTION.md`
- General bounded provider rules: `BOUNDED_PROVIDER_VALIDATION_PLAN.md`
