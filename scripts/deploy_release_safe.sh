#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DEPLOY="$SCRIPT_DIR/deploy_release.sh"
SOURCE_LIB="$SCRIPT_DIR/deploy_release_lib.sh"
SOURCE_SCANNER="$SCRIPT_DIR/scan_evidence_credentials.py"

for path in "$SOURCE_DEPLOY" "$SOURCE_LIB" "$SOURCE_SCANNER"; do
  [[ -f "$path" ]] || {
    printf 'ERROR: required deployment tool missing: %s\n' "$path" >&2
    exit 1
  }
done

PATCH_DIR="$(mktemp -d)"
cleanup() { rm -rf -- "$PATCH_DIR"; }
trap cleanup EXIT

cp "$SOURCE_LIB" "$PATCH_DIR/deploy_release_lib.sh"
cp "$SOURCE_SCANNER" "$PATCH_DIR/scan_evidence_credentials.py"

python3 - "$SOURCE_DEPLOY" "$PATCH_DIR/deploy_release.sh" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")

old_allowlist = r"""    .github/workflows/deploy-script.yml|docs/PRODUCTION_CUTOVER.md|scripts/deploy_release.sh|scripts/deploy_release_lib.sh)
"""
new_allowlist = r"""    .github/workflows/deploy-script.yml|docs/PRODUCTION_CUTOVER.md|docs/PHASE2D_CREDENTIAL_SCAN_INCIDENT.md|scripts/deploy_release.sh|scripts/deploy_release_lib.sh|scripts/deploy_release_safe.sh|scripts/scan_evidence_credentials.py|tests/test_deploy_release_safe.sh)
"""

old_scan = r"""credential_markers="$(grep -ERic \
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|"refresh_token"[[:space:]]*:|"client_secret"[[:space:]]*:|Authorization: Bearer|Cf-Access-Jwt-Assertion' \
  "$EVIDENCE_ROOT" || true)"
printf 'evidence_raw_credential_marker_count=%s\n' "$credential_markers" >> "$EVIDENCE_ROOT/final-log-scan.txt"
[[ "$credential_markers" == "0" ]] || fail "possible credential content in evidence"
"""
new_scan = r"""if ! credential_markers="$(python3 "$SCRIPT_DIR/scan_evidence_credentials.py" "$EVIDENCE_ROOT")"; then
  fail "credential scan execution failed"
fi
printf 'evidence_raw_credential_marker_count=%s\n' "$credential_markers" >> "$EVIDENCE_ROOT/final-log-scan.txt"
[[ "$credential_markers" == "0" ]] || fail "possible credential content in evidence"
"""

for label, old, new in (
    ("main drift allowlist", old_allowlist, new_allowlist),
    ("credential scan", old_scan, new_scan),
):
    occurrences = text.count(old)
    if occurrences != 1:
        raise SystemExit(
            f"refusing unsafe patch: {label} occurrence count is {occurrences}"
        )
    text = text.replace(old, new, 1)

destination.write_text(text, encoding="utf-8")
PY

chmod 700 "$PATCH_DIR/deploy_release.sh"
chmod 600 "$PATCH_DIR/deploy_release_lib.sh"
chmod 700 "$PATCH_DIR/scan_evidence_credentials.py"

bash -n "$PATCH_DIR/deploy_release.sh"
exec bash "$PATCH_DIR/deploy_release.sh" "$@"
