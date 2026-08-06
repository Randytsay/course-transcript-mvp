#!/usr/bin/env bash
# Shared helpers for deploy_release.sh. This file must be sourced.

check_quiescent_jobs() {
  local output_path="$1"
  python3 - "$DB_PATH" <<'PY' | tee "$output_path"
import json
import sqlite3
import sys
from datetime import UTC, datetime

path = sys.argv[1]
safe_statuses = {
    "awaiting_confirmation",
    "awaiting_review",
    "completed",
    "failed",
    "cancelled",
}
connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
connection.row_factory = sqlite3.Row
try:
    rows = connection.execute(
        "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status ORDER BY status"
    ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    unsafe = {key: value for key, value in counts.items() if key not in safe_statuses and value}
    now = datetime.now(UTC).isoformat()
    leases = int(connection.execute(
        """
        SELECT COUNT(*) FROM jobs
        WHERE locked_by IS NOT NULL
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at > ?
        """,
        (now,),
    ).fetchone()[0])
finally:
    connection.close()
result = {
    "status_counts": counts,
    "unsafe_status_counts": unsafe,
    "unsafe_status_total": sum(unsafe.values()),
    "live_lease_count": leases,
}
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
if result["unsafe_status_total"]:
    raise SystemExit("non-quiescent jobs exist")
if leases:
    raise SystemExit("live worker leases exist")
PY
}

audit_build_env() {
  local output_path="$1"
  if [[ ! -f "$BUILD_ENV" ]]; then
    printf 'file_present=NO\n' | tee "$output_path"
    return 0
  fi
  local mode
  mode="$(stat --format='%a' "$BUILD_ENV")"
  [[ "$mode" == "600" || "$mode" == "400" ]] || fail "unsafe mode on $BUILD_ENV: $mode"
  python3 - "$BUILD_ENV" <<'PY' | tee "$output_path"
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
safe_sensitive_names = {
    "GCP_CREDENTIALS_HOST_PATH",
    "RCLONE_CONFIG_HOST_PATH",
    "GOOGLE_DRIVE_REFRESH_TOKEN_HOST_PATH",
    "BILLING_CREDENTIALS_HOST_PATH",
}
dangerous_name = re.compile(
    r"(TOKEN|PASSWORD|CLIENT_SECRET|PRIVATE_KEY|API_KEY|ACCESS_KEY|AUTHORIZATION|COOKIE)",
    re.IGNORECASE,
)
dangerous_content = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    '"refresh_token"',
    '"client_secret"',
)
keys = []
dangerous_keys = []
for raw in text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[7:].strip()
    if "=" not in line:
        continue
    key = line.split("=", 1)[0].strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise SystemExit("invalid environment key syntax")
    keys.append(key)
    if dangerous_name.search(key) and key not in safe_sensitive_names:
        dangerous_keys.append(key)
if any(marker in text for marker in dangerous_content):
    raise SystemExit("possible raw credential content in build env")
if dangerous_keys:
    raise SystemExit("possible secret variables: " + ",".join(sorted(set(dangerous_keys))))
print("file_present=YES")
print("raw_credential_markers=0")
print("environment_keys=" + ",".join(sorted(set(keys))))
PY
}

validate_compose_render() {
  local rendered
  rendered="$(mktemp /root/course-transcript-cutover.XXXXXX.json)"
  chmod 600 "$rendered"
  TMP_FILES+=("$rendered")
  compose config --format json > "$rendered"
  python3 - "$rendered" "$RELEASE_SHA" "$DATA_ROOT" <<'PY'
import json
import sys
from pathlib import Path

model = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sha = sys.argv[2]
data_root = sys.argv[3]
services = model.get("services") or {}
required = {"api", "worker", "pipeline-worker", "delivery-worker", "frontend"}
missing = sorted(required - set(services))
if missing:
    raise SystemExit(f"missing services: {missing}")
for name in sorted(required):
    image = str(services[name].get("image") or "")
    if not image.endswith(f":{sha}"):
        raise SystemExit(f"incorrect image for {name}: {image}")
for name in ("api", "worker", "pipeline-worker", "delivery-worker"):
    mounts = services[name].get("volumes") or []
    data = next((item for item in mounts if item.get("target") == "/app/data"), None)
    if not data or data.get("source") != data_root:
        raise SystemExit(f"incorrect data mount for {name}")
    rclone = next((item for item in mounts if item.get("target") == "/run/secrets/rclone.conf"), None)
    if not rclone or not bool(rclone.get("read_only")):
        raise SystemExit(f"unsafe rclone mount for {name}")
if any(item.get("target") == "/run/secrets/gcp-sa.json" for item in services["api"].get("volumes", [])):
    raise SystemExit("API unexpectedly mounts GCP credentials")
ports = services["frontend"].get("ports") or []
if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or str(ports[0].get("published")) != "3300":
    raise SystemExit(f"unsafe frontend ports: {ports}")
if "cloudflared" in services:
    raise SystemExit("release compose must not manage cloudflared")
print("compose_render=PASS")
PY
  rm -f "$rendered"
}

validate_images() {
  local service image arch revision
  for service in api worker pipeline-worker delivery-worker frontend; do
    image="${NEW_IMAGES[$service]}"
    docker image inspect "$image" >/dev/null
    arch="$(docker image inspect --format '{{.Architecture}}' "$image")"
    revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")"
    [[ "$arch" == "arm64" ]] || fail "wrong architecture for $image: $arch"
    [[ "$revision" == "$RELEASE_SHA" ]] || fail "revision mismatch for $image"
  done
  local frontend_user
  frontend_user="$(docker image inspect --format '{{.Config.User}}' "${NEW_IMAGES[frontend]}")"
  [[ -n "$frontend_user" && "$frontend_user" != "root" && "$frontend_user" != "0" ]] || fail "frontend image must run as non-root"
  printf 'images=PASS\n'
}

validate_delivery_candidates() {
  local output_path="$1"
  python3 "$RELEASE_ROOT/scripts/diagnose_delivery_candidate.py" \
    --data-dir "$DATA_ROOT" > "$output_path"
  python3 - "$output_path" <<'PY'
import json
import sys
from pathlib import Path

eligible = []
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    row = json.loads(line)
    if bool(row.get("eligible")):
        eligible.append(str(row.get("job_id") or "unknown"))
if eligible:
    raise SystemExit("eligible delivery candidates: " + ",".join(eligible))
print("eligible_delivery_candidates=0")
PY
}

delivery_state_snapshot() {
  local output_path="$1"
  : > "$output_path"
  local file path
  for file in pipeline-manifest.json drive-publish-state.json drive-delivery-state.json; do
    path="${STALE_JOB_DIR}/${file}"
    if [[ -f "$path" ]]; then
      stat --format='%n|size=%s|mtime=%y|mode=%a' "$path" >> "$output_path"
      sha256sum "$path" >> "$output_path"
    else
      printf '%s|missing\n' "$path" >> "$output_path"
    fi
  done
}

verify_exact_service() {
  local service="$1"
  local container expected_image actual_image expected_id state
  container="${LIVE_CONTAINERS[$service]}"
  expected_image="${NEW_IMAGES[$service]}"
  actual_image="$(docker inspect --format '{{.Image}}' "$container")"
  expected_id="$(docker image inspect --format '{{.Id}}' "$expected_image")"
  state="$(docker inspect --format '{{.State.Status}}' "$container")"
  [[ "$actual_image" == "$expected_id" ]] || fail "image mismatch for $service"
  [[ "$state" == "running" ]] || fail "$service is not running"
}

wait_health() {
  local container="$1"
  local attempt status
  for attempt in $(seq 1 60); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container")"
    case "$status" in
      healthy) return 0 ;;
      unhealthy) return 1 ;;
    esac
    sleep 2
  done
  return 1
}

verify_worker_stable() {
  local service="$1"
  local since="$2"
  local container state restarts errors
  container="${LIVE_CONTAINERS[$service]}"
  sleep 10
  state="$(docker inspect --format '{{.State.Status}}' "$container")"
  restarts="$(docker inspect --format '{{.RestartCount}}' "$container")"
  [[ "$state" == "running" ]] || return 1
  [[ "$restarts" == "0" ]] || return 1
  errors="$(docker logs --since "$since" "$container" 2>&1 | grep -Eic 'Traceback|ModuleNotFoundError|ImportError|CRITICAL|FATAL' || true)"
  [[ "$errors" == "0" ]]
}

rollback_all() {
  ((ROLLBACK_RUNNING == 0)) || return 0
  ROLLBACK_RUNNING=1
  ROLLBACK_ARMED=0
  set +e
  printf 'ROLLBACK_STARTED=YES\n' | tee -a "$EVIDENCE_ROOT/rollback-result.txt"
  export COURSE_TRANSCRIPT_RELEASE_TAG="$ROLLBACK_TAG"
  local service
  for service in api worker pipeline-worker delivery-worker frontend; do
    compose up -d --no-build --no-deps --force-recreate "$service" \
      >> "$EVIDENCE_ROOT/rollback-result.txt" 2>&1
  done
  sleep 15
  for service in api worker pipeline-worker delivery-worker frontend; do
    local container actual expected state
    container="${LIVE_CONTAINERS[$service]}"
    actual="$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null)"
    expected="$(docker image inspect --format '{{.Id}}' "${IMAGE_REPOS[$service]}:${ROLLBACK_TAG}" 2>/dev/null)"
    state="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null)"
    printf '%s|state=%s|image_match=%s\n' "$service" "$state" "$([[ "$actual" == "$expected" ]] && echo YES || echo NO)" \
      | tee -a "$EVIDENCE_ROOT/rollback-result.txt"
  done
  export COURSE_TRANSCRIPT_RELEASE_TAG="$RELEASE_SHA"
  printf 'ROLLBACK_FINISHED=YES\n' | tee -a "$EVIDENCE_ROOT/rollback-result.txt"
}

on_exit() {
  local rc=$?
  cleanup_temps
  if ((rc != 0 && ROLLBACK_ARMED == 1)); then
    rollback_all
  fi
  exit "$rc"
}
