#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

cat > "$TMP/deploy_release.sh" <<'SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail
root="$1"
credential_markers="$(grep -ERic \
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|"refresh_token"[[:space:]]*:|"client_secret"[[:space:]]*:|Authorization: Bearer|Cf-Access-Jwt-Assertion' \
  "$root" || true)"
printf '%s\n' "$credential_markers"
SCRIPT
chmod 700 "$TMP/deploy_release.sh"
cp "$ROOT/scripts/deploy_release_safe.sh" "$TMP/deploy_release_safe.sh"
chmod 700 "$TMP/deploy_release_safe.sh"

mkdir "$TMP/clean" "$TMP/dirty"
printf 'ordinary evidence\n' > "$TMP/clean/result.txt"
printf '%s\n' '-----BEGIN PRIVATE KEY-----' > "$TMP/dirty/leak.txt"

clean="$(bash "$TMP/deploy_release_safe.sh" "$TMP/clean")"
dirty="$(bash "$TMP/deploy_release_safe.sh" "$TMP/dirty")"

[[ "$clean" == "0" ]]
[[ "$dirty" == "1" ]]
printf 'DEPLOY_RELEASE_SAFE_TEST=PASS\n'
