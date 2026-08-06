#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_GREP="$(command -v grep)"
[[ -x "$REAL_GREP" ]] || { printf 'ERROR: grep not found\n' >&2; exit 1; }

SHIM_DIR="$(mktemp -d)"
cleanup() { rm -rf -- "$SHIM_DIR"; }
trap cleanup EXIT

cat > "$SHIM_DIR/grep" <<'SHIM'
#!/usr/bin/env bash
set -Eeuo pipefail
REAL_GREP="__REAL_GREP__"

# GNU grep interprets a pattern beginning with '-' as an option unless '--'
# terminates option parsing. Restrict the compatibility shim to the exact
# Phase 2D credential-evidence scan; all other grep calls pass through unchanged.
if [[ $# -ge 3 && "$1" == "-ERic" && "$2" == -----BEGIN* ]]; then
  exec "$REAL_GREP" "$1" -- "${@:2}"
fi
exec "$REAL_GREP" "$@"
SHIM
python3 - "$SHIM_DIR/grep" "$REAL_GREP" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.write_text(path.read_text().replace("__REAL_GREP__", sys.argv[2]), encoding="utf-8")
PY
chmod 700 "$SHIM_DIR/grep"

export PATH="$SHIM_DIR:$PATH"
exec bash "$SCRIPT_DIR/deploy_release.sh" "$@"
