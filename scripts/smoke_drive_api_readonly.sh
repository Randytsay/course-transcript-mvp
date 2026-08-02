#!/usr/bin/env bash
set -euo pipefail

API_CONTAINER="${API_CONTAINER:-course-transcript-api-1}"
PUBLIC_ORIGIN="${COURSE_TRANSCRIPT_PUBLIC_ORIGIN:-https://transcript.randy88.ccwu.cc}"
ACCESS_EMAIL="${SMOKE_ACCESS_EMAIL:-drive-smoke@localhost}"
ACCESS_ASSERTION="${SMOKE_ACCESS_ASSERTION:-readonly-smoke-test}"

run_request() {
  local endpoint="$1"
  local method="$2"
  local body="${3:-}"

  sudo docker exec \
    -e SMOKE_ENDPOINT="$endpoint" \
    -e SMOKE_METHOD="$method" \
    -e SMOKE_BODY="$body" \
    -e SMOKE_ORIGIN="$PUBLIC_ORIGIN" \
    -e SMOKE_EMAIL="$ACCESS_EMAIL" \
    -e SMOKE_ASSERTION="$ACCESS_ASSERTION" \
    "$API_CONTAINER" \
    python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

endpoint = os.environ["SMOKE_ENDPOINT"]
method = os.environ["SMOKE_METHOD"]
body = os.environ.get("SMOKE_BODY", "")
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
        status = response.status
except urllib.error.HTTPError as exc:
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"detail": "non-json error"}
    status = exc.code
elapsed_ms = round((time.monotonic() - started) * 1000)

safe = {
    "endpoint": endpoint,
    "http_status": status,
    "duration_ms": elapsed_ms,
    "status": payload.get("status"),
    "provider": payload.get("provider"),
    "fallback_available": payload.get("fallback_available"),
    "entry_count": len(payload.get("entries", [])) if isinstance(payload.get("entries"), list) else None,
    "next_page_available": bool(payload.get("next_page_token")),
    "warning_present": bool(payload.get("warning")),
    "detail": payload.get("detail") if status >= 400 else None,
}
print(json.dumps(safe, ensure_ascii=False))
if status >= 400:
    raise SystemExit(1)
PY
}

echo "DRIVE_API_READONLY_SMOKE"
run_request "/api/v1/drive/health" "GET"
run_request "/api/v1/drive/browse" "POST" '{"source_path":"gdrive:","page_size":20}'

echo "paid_provider_calls_made=NO"
echo "drive_mutations_made=NO"
echo "secrets_printed=NO"
