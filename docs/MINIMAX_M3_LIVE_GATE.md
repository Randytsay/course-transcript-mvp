# MiniMax M3 Windowed Correction — ARM64/VPS Live Acceptance Gate

## Purpose

This is the paid-provider acceptance runbook for the MiniMax M3 windowed correction path merged through PR #68 and wired to the production `M3_FIRST` policy through PR #74.

The gate validates the **current non-streaming provider router** against the exact historical corpus that exposed PR #50's course-level fallback weakness. It does not revive Streaming 2.0 and it does not enable M3 in production.

The required production invariant remains:

```text
Chirp 3 = immutable ASR text/timing source
MiniMax M3 = text-only correction candidate
human review/versioning = final publication boundary
```

Passing this document is evidence for a later production-enable proposal. It is **not** authorization to change production flags.

## Mandatory safety boundary

Before any command in this document:

1. The exact approved `main` SHA must already have passed `docs/VPS_DEPLOY_GATE.md` on the ARM64 VPS.
2. The read-only active-job query from that gate must report zero active or leased paid jobs.
3. The database/jobs backup from that gate must exist and be verified.
4. Production must still report:

```text
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
```

5. The user must separately approve the exact bounded provider stage being run.

This gate must never:

- edit the production `.env`;
- recreate/restart production services merely to test M3;
- write `subtitles-corrected.json`, review state, SQLite job state, or Drive files;
- call Chirp or Gemini;
- upload/publish/rollback YouTube captions;
- print or copy the MiniMax API key;
- persist transcript text, prompts, provider response bodies, or raw model output in gate evidence;
- silently substitute different jobs when a historical corpus job is missing.

If any required source is missing or has malformed subtitle artifacts, stop and report `M3_GATE_STOP`.

## Historical corpus identity

These identities come from the sanitized PR #50 VPS evidence commit `950dd1290ffd1c5d485d9497c6027d3709713eea`.

| Role | Existing job/source |
|---|---|
| bounded varied-source sample | `260815-20260816-152635-39ffbc` |
| historical sample SHA-256 | `9866acfb806243caf403dbfc778229b9d2bd2f44a4caadd937367c71c8d564ac` |
| bounded sample scope | first 107 source segments |
| Course A | `260801-1934-20260801-205446-9e6ecc` |
| Course B / old blocker course | `09-20260510-20260808-154719-24752c` |
| old blocker segment | `seg-0338` |
| Course C | `260718-20260801-100405-f76e03` |

The old PR #50 run used a different streaming/runtime window implementation. **Do not reproduce its window indices.** For this gate, the same source segments must be re-windowed with the current `build_realtime_windows()` implementation (24 segments / 8,000 source characters by default). This validates the code that would actually run after production enablement.

## Approval tokens

The operator must supply approvals one stage at a time:

```text
M3_BLOCKER_APPROVED=yes|no
M3_SAMPLE_APPROVED=yes|no
M3_LONGRUN_APPROVED=yes|no
M3_LONGRUN_MAX_PROVIDER_CALLS=<integer>
```

Rules:

- `M3_BLOCKER_APPROVED=yes` authorizes only the single current window containing `seg-0338`.
- `M3_SAMPLE_APPROVED=yes` authorizes only the first 107 segments of the historical bounded sample.
- `M3_LONGRUN_APPROVED=yes` authorizes only the three exact course IDs above and only after a provider-free dry-run reports the planned window/call ceiling.
- The long-run must stop before generation if `2 × total_planned_windows` exceeds `M3_LONGRUN_MAX_PROVIDER_CALLS`.
- Approval of one stage must never be inferred as approval of the next.

## Phase A — prove production remains off and resolve read-only mounts

Run from `/opt/course-transcript` after the normal VPS deploy gate has passed.

```bash
set -euo pipefail
cd /opt/course-transcript

PW_CONTAINER="$(sudo docker compose \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web ps -q pipeline-worker)"
test -n "${PW_CONTAINER}"

IMAGE_ID="$(sudo docker inspect "${PW_CONTAINER}" --format '{{.Image}}')"
DATA_SOURCE="$(sudo docker inspect "${PW_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}')"
M3_KEY_SOURCE="$(sudo docker inspect "${PW_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/run/secrets/minimax-api-key"}}{{.Source}}{{end}}{{end}}')"
DATA_RW="$(sudo docker inspect "${PW_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.RW}}{{end}}{{end}}')"
KEY_RW="$(sudo docker inspect "${PW_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/run/secrets/minimax-api-key"}}{{.RW}}{{end}}{{end}}')"

test -n "${IMAGE_ID}"
test -n "${DATA_SOURCE}"
test -n "${M3_KEY_SOURCE}"
test -r "${M3_KEY_SOURCE}"

echo "validation_image_id=${IMAGE_ID}"
echo "data_source=${DATA_SOURCE}"
echo "production_data_mount_rw=${DATA_RW}"
echo "production_minimax_key_mount_rw=${KEY_RW}"

sudo docker exec "${PW_CONTAINER}" sh -lc '
  printf "production_m3_enabled=%s\n" "${MINIMAX_M3_ENABLED:-false}"
  printf "production_m3_quota_check=%s\n" "${MINIMAX_M3_QUOTA_CHECK_ENABLED:-false}"
  printf "production_correction_default=%s\n" "${CORRECTION_DEFAULT_POLICY:-GEMINI_FIRST}"
  printf "production_minimax_base=%s\n" "${MINIMAX_API_BASE_URL:-https://api.minimaxi.com}"
'
```

Stop unless the production M3 enable/quota-check flags are both false. The key mount path may be reported; the key contents must never be printed.

The live test container created below mounts `/app/data` **read-only** even though the production worker needs it read/write during normal operation.

## Phase B — install one ephemeral read-only gate runner

Create a temporary script outside the repository. This script emits sanitized metrics only. It never calls the correction artifact writer.

```bash
cat > /tmp/m3-windowed-live-gate.py <<'PY'
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from app.providers.correction.base import ProviderError
from app.providers.correction.orchestrator import (
    CorrectionOrchestrator,
    JobCorrectionSpec,
    build_realtime_windows,
)
from app.providers.correction.registry import (
    AIProviderProfileStore,
    LEGACY_MINIMAX_PROFILE_ID,
)
from app.providers.minimax_quota import MiniMaxQuotaClient

DATA = Path('/app/data')


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return default


def source_segments(job_id: str) -> tuple[list[dict[str, Any]], str, Path]:
    path = DATA / 'jobs' / job_id / 'subtitles.json'
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode('utf-8'))
    items = payload.get('segments') if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError('source subtitles.json missing valid segments')
    segments: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError('source segment is not an object')
        segment_id = str(item.get('segment_id') or '')
        text = str(item.get('raw_text', item.get('text', '')))
        if not segment_id or not text:
            raise RuntimeError('source segment missing id/text')
        segments.append({
            'segment_id': segment_id,
            'text': text,
            'raw_text': text,
            'start_ms': int(item['start_ms']),
            'end_ms': int(item['end_ms']),
        })
    return segments, digest, path


def glossary(job_id: str) -> list[dict[str, Any]]:
    payload = read_json(DATA / 'jobs' / job_id / 'glossary' / 'global-terms.json', {})
    terms = payload.get('terms', []) if isinstance(payload, dict) else []
    return [item for item in terms if isinstance(item, dict)]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * p
    lo = math.floor(index)
    hi = math.ceil(index)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


class MeteredClient:
    supports_window_fallback = True

    def __init__(self, inner: Any, calls: list[dict[str, Any]]):
        self.inner = inner
        self.calls = calls

    @property
    def last_response_meta(self) -> dict[str, Any]:
        value = getattr(self.inner, 'last_response_meta', {})
        return value if isinstance(value, dict) else {}

    def realtime_generate(self, prompt: str) -> str:
        started = time.monotonic()
        try:
            value = self.inner.realtime_generate(prompt)
        except ProviderError as exc:
            self.calls.append({
                'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
                'provider_returned_content': False,
                'error_kind': exc.kind,
            })
            raise
        self.calls.append({
            'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
            'provider_returned_content': True,
            'error_kind': None,
        })
        return value


def select_scope(all_segments: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if args.target_segment:
        matches = [
            window for window in build_realtime_windows(all_segments)
            if args.target_segment in {str(s['segment_id']) for s in window['segments']}
        ]
        if len(matches) != 1:
            raise RuntimeError('target segment does not resolve to exactly one current window')
        selected = list(matches[0]['segments'])
        return selected, {
            'scope': 'target_current_window',
            'target_segment': args.target_segment,
            'selected_segments': len(selected),
            'selected_chars': int(matches[0].get('char_count') or 0),
        }
    if args.first_segments:
        if args.first_segments > len(all_segments):
            raise RuntimeError('requested first-segment count exceeds source')
        selected = all_segments[:args.first_segments]
        return selected, {
            'scope': 'first_segments',
            'selected_segments': len(selected),
        }
    return all_segments, {'scope': 'full_course', 'selected_segments': len(all_segments)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('quota', 'dry-run', 'run'), required=True)
    parser.add_argument('--job-id')
    parser.add_argument('--target-segment')
    parser.add_argument('--first-segments', type=int)
    parser.add_argument('--expected-source-sha256')
    args = parser.parse_args()

    if args.mode == 'quota':
        snapshot = MiniMaxQuotaClient().get_quota(force_refresh=True)
        print(json.dumps({'gate': 'quota', **snapshot.safe_dict()}, ensure_ascii=False))
        return 0 if snapshot.state.value == 'available' else 3

    if not args.job_id:
        raise SystemExit('--job-id is required')

    all_segments, before_sha, source_path = source_segments(args.job_id)
    if args.expected_source_sha256 and before_sha != args.expected_source_sha256:
        print(json.dumps({
            'gate': 'source_identity',
            'job_id': args.job_id,
            'status': 'FAIL',
            'source_sha256': before_sha,
            'expected_source_sha256': args.expected_source_sha256,
        }))
        return 4

    selected, scope = select_scope(all_segments, args)
    planned_windows = build_realtime_windows(selected)
    plan = {
        'gate': args.mode,
        'job_id': args.job_id,
        'source_sha256': before_sha,
        **scope,
        'planned_windows': len(planned_windows),
        'max_provider_calls': 2 * len(planned_windows),
    }
    if args.mode == 'dry-run':
        print(json.dumps(plan, ensure_ascii=False))
        return 0

    calls: list[dict[str, Any]] = []
    profile_store = AIProviderProfileStore(Path('/tmp/m3-live-gate-profile-store'))

    def factory(provider: str, profile_id: str) -> Any:
        if provider != 'minimax' or profile_id != LEGACY_MINIMAX_PROFILE_ID:
            raise RuntimeError('unexpected provider/profile in M3 live gate')
        inner = profile_store.build_client(profile_id, model='MiniMax-M3')
        return MeteredClient(inner, calls)

    orchestrator = CorrectionOrchestrator(run_store=None, client_factory=factory)
    spec = JobCorrectionSpec(
        job_id=args.job_id,
        provider='minimax',
        provider_profile_id=LEGACY_MINIMAX_PROFILE_ID,
        model='MiniMax-M3',
        execution_mode='REALTIME',
        fallback_policy='RAW_CHIRP_FALLBACK',
        source_revision=before_sha,
        source_sha256=before_sha,
    )

    started = time.monotonic()
    try:
        result = orchestrator.correct_realtime(spec, selected, glossary(args.job_id))
    except ProviderError as exc:
        after_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        print(json.dumps({
            **plan,
            'status': 'FATAL_PROVIDER_ERROR',
            'provider_error_kind': exc.kind,
            'source_unchanged': after_sha == before_sha,
            'provider_calls': len(calls),
        }, ensure_ascii=False))
        return 5

    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    corrections = list(result.get('corrections') or [])
    expected_ids = [str(item['segment_id']) for item in selected]
    actual_ids = [str(item.get('segment_id') or '') for item in corrections]
    windows = list(result.get('window_results') or [])
    completed = [item for item in windows if item.get('status') == 'completed']
    fallback = [item for item in windows if item.get('status') == 'fallback_raw_chirp']
    fallback_segments = sum(int(item.get('segment_count') or 0) for item in fallback)
    usage_complete = all(bool(item.get('usage_present')) for item in completed)
    finish_complete = all(item.get('finish_reason') == 'stop' for item in completed)
    latencies = [float(item['elapsed_ms']) for item in calls]
    after_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()

    report = {
        **plan,
        'status': 'COMPLETED',
        'elapsed_ms': elapsed_ms,
        'provider_calls': len(calls),
        'provider_calls_returned_content': sum(bool(item['provider_returned_content']) for item in calls),
        'provider_error_kinds': sorted({str(item['error_kind']) for item in calls if item['error_kind']}),
        'completed_windows': len(completed),
        'fallback_windows': len(fallback),
        'fallback_segments': fallback_segments,
        'route_coverage_percent': round(100 * len(completed) / len(windows), 4) if windows else 0.0,
        'safe_final_completion': len(corrections) == len(selected),
        'segment_ids_exact': actual_ids == expected_ids,
        'finish_reason_stop_all_completed': finish_complete,
        'usage_present_all_completed': usage_complete,
        'provider_circuit_opened': bool(result.get('provider_circuit_opened', False)),
        'source_unchanged': after_sha == before_sha,
        'latency_ms_p50': round(percentile(latencies, 0.50) or 0, 1),
        'latency_ms_p95': round(percentile(latencies, 0.95) or 0, 1),
        'latency_ms_max': round(max(latencies), 1) if latencies else 0,
    }
    print(json.dumps(report, ensure_ascii=False))

    required = (
        report['safe_final_completion']
        and report['segment_ids_exact']
        and report['source_unchanged']
        and report['finish_reason_stop_all_completed']
        and report['usage_present_all_completed']
    )
    return 0 if required else 6


if __name__ == '__main__':
    raise SystemExit(main())
PY

chmod 0500 /tmp/m3-windowed-live-gate.py
```

The script itself contains no secrets. Remove it after the gate.

## Phase C — helper for isolated read-only execution

Use the exact production worker image already validated by `VPS_DEPLOY_GATE`, but do not use `docker compose run` because the normal service has a writable `/app/data` mount.

```bash
m3_gate_run() {
  sudo docker run --rm \
    --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m \
    -v "${DATA_SOURCE}:/app/data:ro" \
    -v "${M3_KEY_SOURCE}:/run/secrets/minimax-api-key:ro" \
    -v /tmp/m3-windowed-live-gate.py:/gate.py:ro \
    -e MINIMAX_API_KEY_FILE=/run/secrets/minimax-api-key \
    -e MINIMAX_API_BASE_URL=https://api.minimaxi.com \
    -e MINIMAX_M3_QUOTA_URL=https://api.minimaxi.com/v1/token_plan/remains \
    -e MINIMAX_M3_MODEL=MiniMax-M3 \
    "${IMAGE_ID}" \
    python /gate.py "$@"
}
```

This container has network access only because MiniMax must be called. Production containers are not restarted or reconfigured.

## Phase D — live quota pre-check (no generation)

```bash
m3_gate_run --mode quota | tee /tmp/m3-quota-before.json
```

Proceed only when the sanitized output reports:

```text
state=available
```

`UNKNOWN` and `UNAVAILABLE` are both stop conditions. Do not bypass the quota gate by calling generation directly.

## Phase E — provider-free planning

Dry-run all exact scopes before spending Token Plan allowance:

```bash
m3_gate_run --mode dry-run \
  --job-id 09-20260510-20260808-154719-24752c \
  --target-segment seg-0338

m3_gate_run --mode dry-run \
  --job-id 260815-20260816-152635-39ffbc \
  --first-segments 107 \
  --expected-source-sha256 9866acfb806243caf403dbfc778229b9d2bd2f44a4caadd937367c71c8d564ac

for JOB in \
  260801-1934-20260801-205446-9e6ecc \
  09-20260510-20260808-154719-24752c \
  260718-20260801-100405-f76e03
do
  m3_gate_run --mode dry-run --job-id "${JOB}"
done
```

Record only job IDs, source hashes, segment/window counts, and max-provider-call counts. Do not print transcript text.

Before the three-course stage, sum all `planned_windows` and compute:

```text
absolute_worst_case_provider_calls = 2 × total_planned_windows
```

Do not begin the three-course stage until the user has explicitly supplied `M3_LONGRUN_APPROVED=yes` and a `M3_LONGRUN_MAX_PROVIDER_CALLS` that covers the dry-run ceiling.

## Phase F — known PR #50 blocker window

Run only when `M3_BLOCKER_APPROVED=yes`.

```bash
m3_gate_run --mode run \
  --job-id 09-20260510-20260808-154719-24752c \
  --target-segment seg-0338 \
  | tee /tmp/m3-blocker.json
```

This stage is a hard stop gate. Require all of:

```text
status=COMPLETED
planned_windows=1
completed_windows=1
fallback_windows=0
route_coverage_percent=100
safe_final_completion=true
segment_ids_exact=true
finish_reason_stop_all_completed=true
usage_present_all_completed=true
provider_circuit_opened=false
source_unchanged=true
```

If this current window falls back, returns an auth/quota error, or violates any invariant, stop. Do not run the sample or long courses.

The historical HTTP 422 itself does not need to recur: the purpose is to prove that the **current** request/window/router handles the same source area safely and natively.

## Phase G — bounded historical source sample

Run only after Phase F passes and `M3_SAMPLE_APPROVED=yes`.

```bash
m3_gate_run --mode run \
  --job-id 260815-20260816-152635-39ffbc \
  --first-segments 107 \
  --expected-source-sha256 9866acfb806243caf403dbfc778229b9d2bd2f44a4caadd937367c71c8d564ac \
  | tee /tmp/m3-sample.json
```

This intentionally uses the current 24-segment/8,000-character router windows. The number of windows is therefore not required to equal PR #49's historical 11 streaming windows.

Require:

```text
route_coverage_percent=100
fallback_windows=0
safe_final_completion=true
segment_ids_exact=true
finish_reason_stop_all_completed=true
usage_present_all_completed=true
provider_circuit_opened=false
source_unchanged=true
```

If the historical source SHA no longer matches, stop rather than silently changing the comparison corpus.

## Phase H — exact three-course long run

Run only after Phases F/G pass and after the provider-free dry-run ceiling is explicitly approved.

Create a sanitized evidence directory outside the repository:

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE="/opt/course-transcript-backups/m3-windowed-gate-${STAMP}"
sudo mkdir -p "${EVIDENCE}"
sudo chown "$(id -u):$(id -g)" "${EVIDENCE}"
chmod 0700 "${EVIDENCE}"
```

Then run each course separately, stopping on fatal/invariant failure:

```bash
m3_gate_run --mode run \
  --job-id 260801-1934-20260801-205446-9e6ecc \
  | tee "${EVIDENCE}/course-a.json"

m3_gate_run --mode run \
  --job-id 09-20260510-20260808-154719-24752c \
  | tee "${EVIDENCE}/course-b.json"

m3_gate_run --mode run \
  --job-id 260718-20260801-100405-f76e03 \
  | tee "${EVIDENCE}/course-c.json"
```

Do not run multiple courses concurrently. The Token Plan gate and provider behavior must remain attributable to one source at a time.

### Per-course technical acceptance

Each course must satisfy:

- `safe_final_completion=true`;
- `segment_ids_exact=true`;
- `source_unchanged=true`;
- `finish_reason_stop_all_completed=true`;
- `usage_present_all_completed=true`;
- `route_coverage_percent >= 95.0`;
- no fatal auth/quota error;
- no timestamp/artifact mutation (the source is read-only and SHA must match);
- provider call count must not exceed that course's dry-run `max_provider_calls`.

A small number of content/request-specific fallback windows is permitted only within the 5% route-coverage allowance. Circuit opening is a technical gate failure even if raw fallback gives safe final completion, because it indicates provider-level instability.

### Aggregate acceptance

Compute aggregate route coverage using counts, not an unweighted average:

```text
aggregate_route_coverage =
  sum(completed_windows) / sum(planned_windows) × 100
```

Require aggregate route coverage `>= 95.0%` and 100% safe final completion across all three courses.

## Phase I — quota post-check and production readback

After live calls:

```bash
m3_gate_run --mode quota | tee "${EVIDENCE}/quota-after.json"

sudo docker exec "${PW_CONTAINER}" sh -lc '
  printf "production_m3_enabled=%s\n" "${MINIMAX_M3_ENABLED:-false}"
  printf "production_m3_quota_check=%s\n" "${MINIMAX_M3_QUOTA_CHECK_ENABLED:-false}"
  printf "production_correction_default=%s\n" "${CORRECTION_DEFAULT_POLICY:-GEMINI_FIRST}"
'

sudo docker compose \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web ps
```

Then rerun the read-only active/leased-job query from `docs/VPS_DEPLOY_GATE.md`.

Mandatory final state after the gate:

```text
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
active_or_leased_jobs=0
production_services_healthy=true
```

If production flags changed, treat the gate as failed even when model metrics passed.

## Phase J — quality review is a separate hold point

Technical reliability is necessary but not sufficient for subtitle proofreading quality.

The gate runner intentionally does not persist corrected text. If technical acceptance passes, a separate explicitly approved quality-review step may compare M3 output against retained Chirp/Gemini/human-reviewed evidence **locally**. Do not create new Gemini calls merely to score this gate, and do not commit raw transcript or model output to GitHub.

Production enablement requires both:

```text
M3_TECHNICAL_GATE=PASS
M3_QUALITY_REVIEW=PASS
```

If technical metrics pass but quality has not been reviewed, the correct state is:

```text
READY_FOR_M3_PRODUCTION=NO
NEXT_GATE=QUALITY_REVIEW
```

## Sanitized report format

Return only bounded metadata:

```text
M3_WINDOWED_LIVE_GATE
approved_sha: <exact main sha>
architecture: aarch64
production_m3_enabled_before: false
production_quota_check_before: false
production_default_policy_before: GEMINI_FIRST
quota_before: AVAILABLE|UNAVAILABLE|UNKNOWN
blocker_job: 09-20260510-20260808-154719-24752c
blocker_segment: seg-0338
blocker_native_route: PASS|FAIL
sample_job: 260815-20260816-152635-39ffbc
sample_source_sha_match: PASS|FAIL
sample_route_coverage: <percent>
course_a_route_coverage: <percent>
course_b_route_coverage: <percent>
course_c_route_coverage: <percent>
aggregate_route_coverage: <percent>
safe_final_completion: PASS|FAIL
segment_id_invariants: PASS|FAIL
source_sha_invariants: PASS|FAIL
usage_on_completed_windows: PASS|FAIL
finish_reason_stop: PASS|FAIL
provider_circuit_opened: YES|NO
provider_calls_total: <integer>
provider_calls_approved_ceiling: <integer>
quota_after: AVAILABLE|UNAVAILABLE|UNKNOWN
production_m3_enabled_after: false
production_quota_check_after: false
production_default_policy_after: GEMINI_FIRST
active_or_leased_jobs_after: 0
production_services_healthy_after: PASS|FAIL
chirp_calls_made: 0
gemini_calls_made: 0
drive_mutations_made: 0
accepted_artifacts_written: 0
secrets_printed: NO
raw_transcript_or_provider_body_persisted: NO
M3_TECHNICAL_GATE: PASS|FAIL
M3_QUALITY_REVIEW: PASS|PENDING|FAIL
READY_FOR_M3_PRODUCTION: NO
next_gate: QUALITY_REVIEW|FIX_AND_REPEAT|SEPARATE_PRODUCTION_ENABLE_REVIEW
```

Do not include transcript text, prompts, corrected text, API response bodies, Authorization headers, or API keys in this report.

## Pass/fail decision

`M3_TECHNICAL_GATE=PASS` requires all of the following:

1. exact historical blocker source area completes natively with no fallback;
2. bounded 107-segment source sample completes with 100% M3 route coverage;
3. each of the three long courses reaches at least 95% M3 route coverage;
4. aggregate route coverage is at least 95%;
5. all selected segments have safe final completion and exact segment IDs;
6. every completed M3 window has `finish_reason=stop` and usage metadata;
7. no provider circuit opens;
8. all source hashes remain unchanged;
9. provider calls stay within the explicitly approved ceiling;
10. quota checks remain non-ambiguous and production remains healthy/off afterward.

Any hard invariant failure is a FAIL even when the percentage threshold would otherwise pass.

## After a technical PASS

Do **not** edit `.env` and do **not** turn on `MINIMAX_M3_ENABLED` from this gate.

The next steps are deliberately separate:

1. local quality review against retained evidence;
2. reviewed minimal production-enable PR/config plan;
3. one production canary with explicit owner approval;
4. immediate rollback to Gemini-first if the canary violates route, quality, latency, or quota expectations.

Streaming remains out of scope. It may only be reconsidered as a transport implementation detail inside `MiniMaxCorrectionProvider` if the current non-streaming windowed path fails a demonstrated reliability/latency requirement.

## Cleanup

After reporting:

```bash
rm -f /tmp/m3-windowed-live-gate.py \
      /tmp/m3-quota-before.json \
      /tmp/m3-blocker.json \
      /tmp/m3-sample.json
```

Keep only the sanitized long-run evidence directory if it was created. Never copy the MiniMax secret into that directory.
