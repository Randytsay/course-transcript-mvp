# MiniMax M3 HTTP 422 diagnostic gate

## Purpose

This is the next bounded paid-provider gate after the historical `seg-0338`
window continued to fail with HTTP 422 even after the now-retired CN 2048
workaround.

The goal is **diagnosis, not acceptance**: make exactly one MiniMax M3
generation request through the production adapter and capture only the
adapter's sanitized `ProviderError.kind` and `ProviderError.safe_message`.

Current MiniMax M3 OpenAI-compatible documentation supports the existing
request shape used by the adapter, including:

- `POST /v1/chat/completions`;
- `model=MiniMax-M3`;
- `thinking={"type":"disabled"}`;
- `reasoning_split=true`;
- `temperature=0.2`;
- `max_completion_tokens=4096` (well within the current M3 documented limit).

Therefore this gate must not mutate request fields merely to make the call
succeed. It exists to reveal the provider's safe structural rejection metadata.

## Required approvals

Before the live request, require a new explicit approval:

```text
M3_422_DIAGNOSTIC_APPROVED=yes
DEPLOYED_RUNTIME_SHA=<exact deployed SHA containing the safe diagnostics patch>
MAX_GENERATION_CALLS=1
```

Prior blocker/retest approvals do not authorize this call.

## Production roots

```text
SOURCE_REPO=/opt/course-transcript-source
DATA_ROOT=/opt/course-transcript-source/data
PROJECT=course-transcript-source
ENV_FILE=/home/ubuntu/.env
```

`/opt/course-transcript` is a historical snapshot and is not the source repo.

## Hard preflight

Resolve the single running `pipeline-worker` using Compose labels and require:

```text
running image revision == DEPLOYED_RUNTIME_SHA
architecture == arm64/aarch64
/app/data source == /opt/course-transcript-source/data
MiniMax key mount exists and is read-only
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
active_or_leased_jobs_before=0
```

Any mismatch is `STOP` with zero generation calls.

Do not restart/recreate production services, edit `.env`, change the source
checkout, or build an image inside this diagnostic gate.

## Exact diagnostic scope

```text
job_id=09-20260510-20260808-154719-24752c
target_segment=seg-0338
expected_window=corr-v2:rt:seg-0337..seg-0360
expected_source_sha256=816f4fb9c113692bf2a0e26ad9422da11a2a416e586db604117e1902cdd19100
provider=MiniMax-M3
endpoint=https://api.minimaxi.com/v1/chat/completions
max_generation_calls=1
```

Use current production `build_realtime_windows()` to resolve the target window.
Require exactly one matching window and the expected source hash before any
provider call.

## Isolated runner

Run the diagnostic in an ephemeral container created from the exact running
`pipeline-worker` image:

```text
--rm
--read-only
/tmp = tmpfs
/app/data = production data mounted :ro
/run/secrets/minimax-api-key = existing production secret mounted :ro
```

The runner must not mount writable production data or call any artifact writer.

The runner should:

1. perform one quota pre-check with `MiniMaxQuotaClient().get_quota(force_refresh=True)`;
2. stop unless quota state is `AVAILABLE`;
3. load the immutable source and resolve the exact target window;
4. build the exact production correction prompt with `build_user_prompt()`;
5. construct the provider through `AIProviderProfileStore` and
   `legacy-minimax-token-plan`;
6. call `MiniMaxCorrectionProvider.realtime_generate(prompt)` exactly once;
7. catch `ProviderError` directly, with no `CorrectionOrchestrator`, retry, or
   raw-Chirp fallback;
8. print only bounded safe metadata.

## Safe error contract

The patched provider may expose structural information such as:

```text
provider code=<bounded identifier>
error_type=<bounded identifier>
param=<bounded identifier>
validation=loc:<bounded dotted location>,type:<bounded identifier>
category=content_rejected
```

It must never expose or persist:

- provider response body;
- arbitrary provider `message` / validation `msg` / validation `input`;
- prompt or transcript text;
- corrected text;
- API key or Authorization header;
- request body or request headers.

If a provider error contains only arbitrary message text with no allow-listed
structure, the safe output may remain simply `invalid_request + HTTP 422`.

If known provider text indicates moderation/content rejection, the adapter may
emit only the categorical marker `category=content_rejected`; the original text
must not be echoed.

## One-call ceiling

The diagnostic has an absolute generation ceiling of one.

```text
provider_calls_before=0
provider_calls_after=1
```

No retry wrapper, no orchestrator, no curl fallback, no second endpoint, no
second model, and no manual rerun are allowed under this approval.

## Post-check

After the single call, require:

```text
source SHA unchanged
active_or_leased_jobs_after=0
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
accepted_artifacts_written=0
sqlite_writes=0
chirp_calls=0
gemini_calls=0
drive_mutations=0
youtube_mutations=0
production services restarted/recreated=0
secrets_printed=NO
```

## Sanitized report

```text
M3_422_DIAGNOSTIC_GATE
runbook_git_sha:
deployed_runtime_sha:
job_id: 09-20260510-20260808-154719-24752c
target_segment: seg-0338
window_id:
selected_segments:
selected_chars:
source_sha256_before:
source_sha256_after:
quota_before: AVAILABLE|UNAVAILABLE|UNKNOWN
provider_calls:
diagnostic_result: SUCCESS|PROVIDER_ERROR|STOP
provider_error_kind:
provider_safe_message:
source_unchanged: PASS|FAIL
active_or_leased_jobs_before:
active_or_leased_jobs_after:
production_m3_enabled_after: false
production_m3_quota_check_after: false
production_default_policy_after: GEMINI_FIRST
accepted_artifacts_written: 0
sqlite_writes: 0
chirp_calls: 0
gemini_calls: 0
drive_mutations: 0
youtube_mutations: 0
services_restarted: 0
services_recreated: 0
secrets_printed: NO
FINAL_STATUS: PASS|FAIL|STOP
NEXT_ACTION: RETURN_DIAGNOSTIC_TO_OWNER
```

`FINAL_STATUS=PASS` means only that the diagnostic safely obtained either a
successful response or useful bounded failure evidence. It does **not** change
the historical blocker gate from FAIL and does not authorize the 107-segment
sample.

## Hard stop

After the one provider call, stop regardless of success or failure. Do not run
another blocker test, sample, long run, production M3 enablement, Drive action,
or YouTube action.
