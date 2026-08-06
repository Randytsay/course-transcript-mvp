#!/usr/bin/env bash
set -euo pipefail

: "${API_CONTAINER:?Set API_CONTAINER to the isolated PR #10 API container ID or name}"

PUBLIC_ORIGIN="${COURSE_TRANSCRIPT_PUBLIC_ORIGIN:-https://transcript.randy88.ccwu.cc}"
ACCESS_EMAIL="${SMOKE_ACCESS_EMAIL:-drive-smoke@localhost}"
ACCESS_ASSERTION="${SMOKE_ACCESS_ASSERTION:-readonly-smoke-test}"
EXPECTED_PROVIDER="${SMOKE_EXPECTED_PROVIDER:-google_api}"
EXPECTED_HEALTH_STATUS="${SMOKE_EXPECTED_HEALTH_STATUS:-ok}"
EXPECTED_FALLBACK_AVAILABLE="${SMOKE_EXPECTED_FALLBACK_AVAILABLE:-false}"
EXPECTED_COMPOSE_PROJECT="${SMOKE_EXPECTED_COMPOSE_PROJECT:-course-transcript-pr10-smoke}"

validate_container() {
  sudo docker inspect "$API_CONTAINER" >/dev/null

  sudo docker inspect "$API_CONTAINER" \
    -e EXPECTED_COMPOSE_PROJECT="$EXPECTED_COMPOSE_PROJECT" \
    >/dev/null 2>&1 || true

  sudo docker inspect "$API_CONTAINER" | python - "$EXPECTED_COMPOSE_PROJECT" <<'PY'
import json
import sys

containers = json.load(sys.stdin)
if len(containers) != 1:
    raise SystemExit("SMOKE_PREFLIGHT=FAIL expected exactly one container")
container = containers[0]
expected_project = sys.argv[1]
labels = container.get("Config", {}).get("Labels") or {}
project = labels.get("com.docker.compose.project")
errors = []
if project != expected_project:
    errors.append(f"compose project is {project!r}, expected {expected_project!r}")

ports = container.get("NetworkSettings", {}).get("Ports") or {}
if any(bindings for bindings in ports.values()):
    errors.append("container publishes a host port")

mounts = container.get("Mounts") or []
by_destination = {str(item.get("Destination")): item for item in mounts}
rclone = by_destination.get("/run/secrets/rclone.conf")
if not rclone:
    errors.append("missing /run/secrets/rclone.conf mount")
elif bool(rclone.get("RW")):
    errors.append("rclone.conf mount is writable")

data = by_destination.get("/app/data")
if not data:
    errors.append("missing isolated /app/data mount")
else:
    source = str(data.get("Source") or "")
    forbidden = {
        "/opt/course-transcript/data",
        "/opt/course-transcript-source/data",
    }
    if source.rstrip("/") in forbidden:
        errors.append("/app/data points to a live data directory")

for item in mounts:
    destination = str(item.get("Destination") or "")
    source = str(item.get("Source") or "")
    if destination.endswith("gcp-sa.json") or source.endswith("gcp-sa.json"):
        errors.append("GCP service-account secret is mounted")

safe = {
    "compose_project": project,
    "host_ports_published": any(bindings for bindings in ports.values()),
    "rclone_config_mounted": bool(rclone),
    "rclone_config_read_only": bool(rclone) and not bool(rclone.get("RW")),
    "isolated_data_mounted": bool(data),
    "gcp_service_account_mounted": any(
        str(item.get("Destination") or "").endswith("gcp-sa.json")
        or str(item.get("Source") or "").endswith("gcp-sa.json")
        for item in mounts
    ),
}
print(json.dumps(safe, ensure_ascii=False))
if errors:
    print(json.dumps({"preflight_errors": errors}, ensure_ascii=False))
    raise SystemExit(1)
PY
}

run_request() {
  local endpoint="$1"
  local method="$2"
  local kind="$3"
  local body="${4:-}"

  sudo docker exec -i \
    -e SMOKE_ENDPOINT="$endpoint" \
    -e SMOKE_METHOD="$method" \
    -e SMOKE_KIND="$kind" \
    -e SMOKE_BODY="$body" \
    -e SMOKE_ORIGIN="$PUBLIC_ORIGIN" \
    -e SMOKE_EMAIL="$ACCESS_EMAIL" \
    -e SMOKE_ASSERTION="$ACCESS_ASSERTION" \
    -e SMOKE_EXPECTED_PROVIDER="$EXPECTED_PROVIDER" \
    -e SMOKE_EXPECTED_HEALTH_STATUS="$EXPECTED_HEALTH_STATUS" \
    -e SMOKE_EXPECTED_FALLBACK_AVAILABLE="$EXPECTED_FALLBACK_AVAILABLE" \
    "$API_CONTAINER" \
    python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

endpoint = os.environ["SMOKE_ENDPOINT"]
method = os.environ["SMOKE_METHOD"]
kind = os.environ["SMOKE_KIND"]
body = os.environ.get("SMOKE_BODY", "")
expected_provider = os.environ["SMOKE_EXPECTED_PROVIDER"]
expected_health_status = os.environ["SMOKE_EXPECTED_HEALTH_STATUS"]
expected_fallback = os.environ["SMOKE_EXPECTED_FALLBACK_AVAILABLE"].lower() in {
    "1", "true", "yes"
}
headers = {
    "Accept": "application/json",
    "Origin": os.environ["SMOKE_ORIGIN"],
    "Cf-Access-Authenticated-User-Email": os.environ["SMOKE_EMAIL"],
    "Cf-Access-Jwt-Assertion": os.environ["SMOKE_ASSERTION"],
}
data = None
if body:
    data = body.encode("utf-8")
    headers["Content-Type"] = "application/json"
request = urllib.request.Request(
    f"http://127.0.0.1:8000{endpoint}",
    data=data,
    headers=headers,
    method=method,
)
started = time.monotonic()
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
        http_status = response.status
except urllib.error.HTTPError as exc:
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"detail": "non-json error"}
    http_status = exc.code
except (OSError, TimeoutError, json.JSONDecodeError) as exc:
    payload = {"detail": type(exc).__name__}
    http_status = 0
elapsed_ms = round((time.monotonic() - started) * 1000)

safe = {
    "endpoint": endpoint,
    "http_status": http_status,
    "duration_ms": elapsed_ms,
    "status": payload.get("status"),
    "provider": payload.get("provider"),
    "fallback_available": payload.get("fallback_available"),
    "account_available": payload.get("account_available") if kind == "health" else None,
    "entry_count": len(payload.get("entries", [])) if isinstance(payload.get("entries"), list) else None,
    "next_page_available": bool(payload.get("next_page_token")),
    "warning_present": bool(payload.get("warning")),
    "paid_operation_started": payload.get("paid_operation_started"),
    "detail": payload.get("detail") if http_status != 200 else None,
}
print(json.dumps(safe, ensure_ascii=False))

errors = []
if http_status != 200:
    errors.append(f"HTTP status is {http_status}, expected 200")
if payload.get("provider") != expected_provider:
    errors.append(
        f"provider is {payload.get('provider')!r}, expected {expected_provider!r}"
    )
if payload.get("paid_operation_started") is not False:
    errors.append("paid_operation_started is not false")
if payload.get("warning"):
    errors.append("response contains a warning")

if kind == "health":
    if payload.get("status") != expected_health_status:
        errors.append(
            f"health status is {payload.get('status')!r}, expected {expected_health_status!r}"
        )
    if payload.get("fallback_available") is not expected_fallback:
        errors.append(
            "fallback_available does not match the strict acceptance setting"
        )
    if payload.get("account_available") is not True:
        errors.append("Google Drive account is not available")
elif kind == "browse":
    if not isinstance(payload.get("entries"), list):
        errors.append("browse response does not contain an entries list")
else:
    errors.append(f"unknown smoke kind {kind!r}")

if errors:
    print(json.dumps({"validation_errors": errors}, ensure_ascii=False))
    raise SystemExit(1)
PY
}

echo "DRIVE_API_READONLY_SMOKE"
validate_container
run_request "/api/v1/drive/health" "GET" "health"
run_request "/api/v1/drive/browse" "POST" "browse" '{"source_path":"gdrive:","page_size":20}'

echo "drive_api_read_calls_made=YES"
echo "drive_mutations_made=NO"
echo "chirp_gemini_billing_calls_made=NO"
echo "credential_source=READ_ONLY_MOUNT"
echo "credentials_printed=NO"
