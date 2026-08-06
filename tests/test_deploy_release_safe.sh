#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

bash -n "$ROOT/scripts/deploy_release.sh"
bash -n "$ROOT/scripts/deploy_release_safe.sh"
python3 -m py_compile "$ROOT/scripts/scan_evidence_credentials.py"

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

printf 'DEPLOY_RELEASE_SAFE_TEST=PASS\n'
