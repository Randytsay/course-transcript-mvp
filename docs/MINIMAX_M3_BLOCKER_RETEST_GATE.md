# MiniMax M3 blocker retest gate — immutable production layout

This is the current authoritative runbook for the **single-window paid retest** of the historical MiniMax blocker at `seg-0338`.

It exists because the older `MINIMAX_M3_LIVE_GATE.md` still contains commands from the pre-immutable production layout. Do not use those old path/compose commands for this retest.

## Scope

The retest is deliberately narrow:

- deployed runtime: exact image revision that already passed `VPS_DEPLOY_GATE`;
- job: `09-20260510-20260808-154719-24752c`;
- target: `seg-0338`;
- expected current window: `corr-v2:rt:seg-0337..seg-0360`;
- provider: MiniMax M3 through the production adapter;
- maximum generation calls: 2, both for the same current window only;
- no Chirp, Gemini, Drive, YouTube, SQLite writes, accepted artifacts, production restart, or production flag changes.

The deployed runtime SHA and the runbook Git SHA are separate concepts. A documentation-only commit may advance `main` without requiring a runtime redeploy. The gate validates the **running pipeline-worker image revision** independently.

## Required explicit approval

Before any generation call, the operator must have an explicit bounded approval:

```text
M3_BLOCKER_RETEST_APPROVED=yes
DEPLOYED_RUNTIME_SHA=<exact already-deployed SHA>
MAX_GENERATION_CALLS=2
```

Approval of this retest does not authorize the 107-segment sample, long runs, production enablement, or any other paid call.

## Production roots

```text
SOURCE_REPO=/opt/course-transcript-source
DATA_ROOT=/opt/course-transcript-source/data
PROJECT=course-transcript-source
ENV_FILE=/home/ubuntu/.env
```

`/opt/course-transcript` is a historical snapshot and must not be used as the deployment/source repo.

## Hard safety gates

Before generation, require all of the following:

```text
running pipeline-worker image revision == DEPLOYED_RUNTIME_SHA
architecture == arm64/aarch64
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
active_or_leased_jobs=0
MiniMax key mount exists and is read-only
/app/data resolves to /opt/course-transcript-source/data
```

Any mismatch is `STOP` with `provider_calls=0`.

Do not edit `.env`, restart or recreate production services, checkout/reset the source repo, or build a replacement image as part of this gate.

## Resolve the running pipeline-worker by Docker labels

Do not depend on an old compose command. Resolve the existing production container from its Compose labels:

```bash
set -euo pipefail

PW_CONTAINER="$(sudo docker ps \
  --filter label=com.docker.compose.project=course-transcript-source \
  --filter label=com.docker.compose.service=pipeline-worker \
  --format '{{.ID}}')"

test -n "${PW_CONTAINER}"
test "$(printf '%s\n' "${PW_CONTAINER}" | wc -l)" -eq 1

IMAGE_ID="$(sudo docker inspect "${PW_CONTAINER}" --format '{{.Image}}')"
IMAGE_REVISION="$(sudo docker image inspect "${IMAGE_ID}" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
IMAGE_ARCH="$(sudo docker image inspect "${IMAGE_ID}" --format '{{.Architecture}}')"
DATA_SOURCE="$(sudo docker inspect "${PW_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}')"
M3_KEY_SOURCE="$(sudo docker inspect "${PW_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/run/secrets/minimax-api-key"}}{{.Source}}{{end}}{{end}}')"
KEY_RW="$(sudo docker inspect "${PW_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/run/secrets/minimax-api-key"}}{{.RW}}{{end}}{{end}}')"

printf 'runtime_revision=%s\n' "${IMAGE_REVISION}"
printf 'runtime_arch=%s\n' "${IMAGE_ARCH}"
printf 'data_source=%s\n' "${DATA_SOURCE}"
printf 'minimax_key_read_only=%s\n' "$([ "${KEY_RW}" = false ] && echo YES || echo NO)"
```

Do not print the key contents or Authorization header.

## Production-off readback

```bash
sudo docker exec "${PW_CONTAINER}" sh -lc '
  printf "production_m3_enabled=%s\n" "${MINIMAX_M3_ENABLED:-false}"
  printf "production_m3_quota_check=%s\n" "${MINIMAX_M3_QUOTA_CHECK_ENABLED:-false}"
  printf "production_correction_default=%s\n" "${CORRECTION_DEFAULT_POLICY:-GEMINI_FIRST}"
  printf "production_minimax_base=%s\n" "${MINIMAX_API_BASE_URL:-https://api.minimaxi.com}"
'
```

Require `false / false / GEMINI_FIRST` and CN base `https://api.minimaxi.com`.

## Read-only active/lease gate

Use the read-only query defined by the already-passed `docs/VPS_DEPLOY_GATE.md` against:

```text
/opt/course-transcript-source/data/course-transcript.db
```

Require `active_or_leased_jobs_before=0`.

## Ephemeral runner

Create `/tmp/m3-blocker-retest.py`. It must never write under `/app/data` and must never invoke the correction artifact writer.

```python
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from app.providers.correction.base import ProviderError
from app.providers.correction.orchestrator import CorrectionOrchestrator, JobCorrectionSpec, build_realtime_windows
from app.providers.correction.registry import AIProviderProfileStore, LEGACY_MINIMAX_PROFILE_ID
from app.providers.minimax_quota import MiniMaxQuotaClient

DATA = Path('/app/data')
JOB_ID = '09-20260510-20260808-154719-24752c'
TARGET = 'seg-0338'
MAX_CALLS = 2


def load_source() -> tuple[list[dict[str, Any]], Path, str]:
    path = DATA / 'jobs' / JOB_ID / 'subtitles.json'
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode('utf-8'))
    items = payload.get('segments') if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError('invalid subtitles source')
    out = []
    for item in items:
        sid = str(item.get('segment_id') or '')
        text = str(item.get('raw_text', item.get('text', '')))
        if not sid or not text:
            raise RuntimeError('invalid source segment')
        out.append({
            'segment_id': sid,
            'text': text,
            'raw_text': text,
            'start_ms': int(item['start_ms']),
            'end_ms': int(item['end_ms']),
        })
    return out, path, digest


class MeteredClient:
    supports_window_fallback = True

    def __init__(self, inner: Any, calls: list[dict[str, Any]]):
        self.inner = inner
        self.calls = calls

    @property
    def last_response_meta(self):
        meta = getattr(self.inner, 'last_response_meta', {})
        return meta if isinstance(meta, dict) else {}

    def realtime_generate(self, prompt: str) -> str:
        if len(self.calls) >= MAX_CALLS:
            raise RuntimeError('provider call ceiling reached before call')
        started = time.monotonic()
        try:
            value = self.inner.realtime_generate(prompt)
        except ProviderError as exc:
            self.calls.append({
                'ok': False,
                'kind': exc.kind,
                'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
            })
            raise
        self.calls.append({
            'ok': True,
            'kind': None,
            'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
        })
        return value


def main() -> int:
    quota = MiniMaxQuotaClient().get_quota(force_refresh=True)
    print(json.dumps({'gate': 'quota', **quota.safe_dict()}, ensure_ascii=False))
    if quota.state.value != 'available':
        return 3

    all_segments, source_path, before_sha = load_source()
    matches = [
        w for w in build_realtime_windows(all_segments)
        if TARGET in {str(s['segment_id']) for s in w['segments']}
    ]
    if len(matches) != 1:
        raise RuntimeError('target does not resolve to one window')

    target_window = matches[0]
    selected = list(target_window['segments'])
    selected_windows = build_realtime_windows(selected)
    if len(selected_windows) != 1:
        raise RuntimeError('selected scope re-windowed to more than one window')

    first_id = str(selected[0]['segment_id'])
    last_id = str(selected[-1]['segment_id'])
    window_id = str(target_window['window_id'])
    if window_id != 'corr-v2:rt:seg-0337..seg-0360':
        raise RuntimeError('current blocker window identity changed')

    calls: list[dict[str, Any]] = []
    store = AIProviderProfileStore(Path('/tmp/m3-live-profile-store'))

    def factory(provider: str, profile_id: str):
        if provider != 'minimax' or profile_id != LEGACY_MINIMAX_PROFILE_ID:
            raise RuntimeError('unexpected provider/profile')
        inner = store.build_client(profile_id, model='MiniMax-M3')
        return MeteredClient(inner, calls)

    orchestrator = CorrectionOrchestrator(run_store=None, client_factory=factory)
    spec = JobCorrectionSpec(
        job_id=JOB_ID,
        provider='minimax',
        provider_profile_id=LEGACY_MINIMAX_PROFILE_ID,
        model='MiniMax-M3',
        execution_mode='REALTIME',
        fallback_policy='RAW_CHIRP_FALLBACK',
        source_revision=before_sha,
        source_sha256=before_sha,
    )

    result = orchestrator.correct_realtime(spec, selected, [])
    after_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()

    corrections = list(result.get('corrections') or [])
    expected_ids = [str(x['segment_id']) for x in selected]
    actual_ids = [str(x.get('segment_id') or '') for x in corrections]
    windows = list(result.get('window_results') or [])
    completed = [x for x in windows if x.get('status') == 'completed']
    fallback = [x for x in windows if x.get('status') == 'fallback_raw_chirp']

    report = {
        'gate': 'M3_BLOCKER_RETEST',
        'job_id': JOB_ID,
        'target_segment': TARGET,
        'window_id': window_id,
        'first_segment_id': first_id,
        'last_segment_id': last_id,
        'selected_segments': len(selected),
        'selected_chars': int(target_window.get('char_count') or 0),
        'source_sha256_before': before_sha,
        'source_sha256_after': after_sha,
        'provider_calls': len(calls),
        'provider_error_kinds': sorted({str(x['kind']) for x in calls if x.get('kind')}),
        'completed_windows': len(completed),
        'fallback_windows': len(fallback),
        'fallback_segment_count': sum(int(x.get('segment_count') or 0) for x in fallback),
        'fallback_reasons': sorted({str(x.get('reason')) for x in fallback if x.get('reason')}),
        'safe_final_completion': len(corrections) == len(selected),
        'segment_ids_exact': actual_ids == expected_ids,
        'finish_reason_stop': len(completed) == 1 and completed[0].get('finish_reason') == 'stop',
        'usage_present': len(completed) == 1 and bool(completed[0].get('usage_present')),
        'provider_circuit_opened': bool(result.get('provider_circuit_opened', False)),
        'source_unchanged': before_sha == after_sha,
    }
    report['route_coverage_percent'] = 100.0 if len(completed) == 1 and not fallback else 0.0
    print(json.dumps(report, ensure_ascii=False))

    passed = (
        len(calls) <= MAX_CALLS
        and len(completed) == 1
        and not fallback
        and report['route_coverage_percent'] == 100.0
        and report['safe_final_completion']
        and report['segment_ids_exact']
        and report['finish_reason_stop']
        and report['usage_present']
        and not report['provider_circuit_opened']
        and report['source_unchanged']
    )
    return 0 if passed else 6


if __name__ == '__main__':
    raise SystemExit(main())
```

The runner intentionally exposes only bounded error kinds and fallback reasons. It must not print transcript text, prompts, corrected text, provider response bodies, headers, or secrets.

## Run it in the exact production image, isolated

```bash
chmod 0500 /tmp/m3-blocker-retest.py

sudo docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  -v "${DATA_SOURCE}:/app/data:ro" \
  -v "${M3_KEY_SOURCE}:/run/secrets/minimax-api-key:ro" \
  -v /tmp/m3-blocker-retest.py:/gate.py:ro \
  -e MINIMAX_API_KEY_FILE=/run/secrets/minimax-api-key \
  -e MINIMAX_API_BASE_URL=https://api.minimaxi.com \
  -e MINIMAX_M3_QUOTA_URL=https://api.minimaxi.com/v1/token_plan/remains \
  -e MINIMAX_M3_MODEL=MiniMax-M3 \
  "${IMAGE_ID}" \
  python /gate.py
```

The current production adapter must cap the CN request to `max_completion_tokens=2048`. This is verified provider-free during deployment; do not alter the payload in the gate runner.

## Acceptance

PASS requires all of:

```text
provider_calls <= 2
completed_windows=1
fallback_windows=0
fallback_segment_count=0
route_coverage_percent=100
safe_final_completion=true
segment_ids_exact=true
finish_reason_stop=true
usage_present=true
provider_circuit_opened=false
source_unchanged=true
```

Any fallback is a blocker FAIL even when raw Chirp fallback yields a complete safe result.

If the request still fails, report `provider_error_kinds` and `fallback_reasons`; do not make a third call, change the endpoint/model/prompt/window, or switch to streaming.

## Post-check

After the ephemeral container exits:

1. rerun the read-only active/lease query and require `active_or_leased_jobs_after=0`;
2. read back production flags and require `false / false / GEMINI_FIRST`;
3. verify no production service was restarted/recreated;
4. verify source SHA is unchanged and no accepted artifact was written;
5. remove `/tmp/m3-blocker-retest.py`.

## Sanitized report

```text
M3_BLOCKER_RETEST_GATE
runbook_git_sha: <Git SHA containing this runbook>
deployed_runtime_sha: <exact running pipeline-worker revision>
job_id: 09-20260510-20260808-154719-24752c
target_segment: seg-0338
window_id: corr-v2:rt:seg-0337..seg-0360
selected_segments: <integer>
selected_chars: <integer>
quota_before: AVAILABLE|UNAVAILABLE|UNKNOWN
provider_calls: <integer>
provider_error_kinds: <safe kinds only>
fallback_reasons: <safe kinds only>
completed_windows: <integer>
fallback_windows: <integer>
fallback_segment_count: <integer>
route_coverage_percent: <number>
safe_final_completion: PASS|FAIL
segment_ids_exact: PASS|FAIL
finish_reason_stop: PASS|FAIL
usage_present: PASS|FAIL
provider_circuit_opened: YES|NO
source_unchanged: PASS|FAIL
active_or_leased_jobs_before: 0
active_or_leased_jobs_after: 0
production_m3_enabled_after: false
production_m3_quota_check_after: false
production_default_policy_after: GEMINI_FIRST
chirp_calls_made: 0
gemini_calls_made: 0
drive_mutations_made: 0
youtube_mutations_made: 0
accepted_artifacts_written: 0
sqlite_writes: 0
services_restarted: 0
services_recreated: 0
secrets_printed: NO
M3_BLOCKER_RETEST_GATE: PASS|FAIL|STOP
NEXT_GATE: WAITING_FOR_SEPARATE_107_SEGMENT_APPROVAL
```

Passing this retest does **not** authorize the next paid stage or production M3 enablement.