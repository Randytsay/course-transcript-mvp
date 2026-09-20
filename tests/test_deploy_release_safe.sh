#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

bash -n "$ROOT/scripts/deploy_release.sh"
bash -n "$ROOT/scripts/deploy_release_safe.sh"
python3 -m py_compile "$ROOT/scripts/scan_evidence_credentials.py"
python3 -m py_compile "$ROOT/scripts/runtime_revision_guard.py"
runtime_guard_self_test="$(python3 "$ROOT/scripts/runtime_revision_guard.py" --self-test)"
[[ "$runtime_guard_self_test" == "RUNTIME_REVISION_GUARD_SELF_TEST=PASS" ]]

mkdir "$TMP/clean" "$TMP/dirty"
printf 'ordinary evidence\n' > "$TMP/clean/result.txt"
printf '%s\n' '-----BEGIN PRIVATE KEY-----' > "$TMP/dirty/private-key.txt"
printf '%s\n' 'prefix "client_secret": "redacted"' > "$TMP/dirty/client.json"

clean="$({
  python3 "$ROOT/scripts/scan_evidence_credentials.py" "$TMP/clean"
})"
dirty="$({
  python3 "$ROOT/scripts/scan_evidence_credentials.py" "$TMP/dirty"
})"

[[ "$clean" == "0" ]]
[[ "$dirty" == "2" ]]

# The scanner must not follow a symlink out of the evidence root.
ln -s "$TMP/dirty" "$TMP/clean/external-evidence"
clean_with_symlink="$({
  python3 "$ROOT/scripts/scan_evidence_credentials.py" "$TMP/clean"
})"
[[ "$clean_with_symlink" == "0" ]]

if python3 "$ROOT/scripts/scan_evidence_credentials.py" "$TMP/missing"; then
  echo "missing evidence directory was accepted"
  exit 1
fi

self_test="$(bash "$ROOT/scripts/deploy_release_safe.sh" --self-test)"
[[ "$self_test" == "SELF_TEST=PASS" ]]

grep -q 'runtime_revision_guard.py.*--expected-sha.*RELEASE_SHA' "$ROOT/scripts/deploy_release.sh"

# health-monitor emits downstream job diagnostics that may themselves contain
# words such as Traceback/CRITICAL. Deployment must validate the fresh report
# and container health instead of treating those payload strings as a crash.
grep -q 'runtime_log_error_count' "$ROOT/scripts/deploy_release_lib.sh"
grep -q 'if \[\[ "\$service" == "health-monitor" \]\]; then' "$ROOT/scripts/deploy_release_lib.sh"
grep -q 'verify_health_monitor_report "\$since"' "$ROOT/scripts/deploy_release.sh"
grep -q 'health-monitor.*final health is not healthy\|service.*health-monitor' "$ROOT/scripts/deploy_release.sh"

# Behavioral regression: health-monitor payload may quote a historical
# traceback without meaning the monitor process crashed.
source "$ROOT/scripts/deploy_release_lib.sh"
declare -A LIVE_CONTAINERS=(
  [health-monitor]="fake-health-monitor"
  [worker]="fake-worker"
)
mkdir -p "$TMP/fake-bin" "$TMP/health-data"
cat > "$TMP/fake-bin/docker" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == "logs" ]]; then
  printf '%s\n' 'WARNING job_failed: historical payload follows: Traceback ... CRITICAL ...'
  exit 0
fi
echo "unexpected fake docker invocation: $*" >&2
exit 2
SH
chmod +x "$TMP/fake-bin/docker"
OLD_PATH="$PATH"
PATH="$TMP/fake-bin:$PATH"
[[ "$(runtime_log_error_count health-monitor '2026-09-20T00:00:00Z')" == "0" ]]
[[ "$(runtime_log_error_count worker '2026-09-20T00:00:00Z')" == "1" ]]
PATH="$OLD_PATH"

DATA_ROOT="$TMP/health-data"
cat > "$DATA_ROOT/production-health.json" <<'JSON'
{"status":"warning","generated_at":"2026-09-20T00:00:01+00:00","counts":{},"findings":[]}
JSON
verify_health_monitor_report '2026-09-20T00:00:00Z' >/dev/null

cat > "$DATA_ROOT/production-health.json" <<'JSON'
{"status":"critical","generated_at":"2026-09-20T00:00:02+00:00","counts":{},"findings":[]}
JSON
if verify_health_monitor_report '2026-09-20T00:00:00Z' >/dev/null 2>&1; then
  echo "critical health report was accepted"
  exit 1
fi

printf 'DEPLOY_RELEASE_SAFE_TEST=PASS\n'
