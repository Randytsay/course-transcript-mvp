#!/usr/bin/env bash
set -euo pipefail

report_path="$1"
test -n "$report_path"
tmp_path="$(mktemp)"
trap 'rm -f "$tmp_path"' EXIT

# Retain only the root-item count; never write Drive folder names to logs.
rclone lsd gdrive: >"$tmp_path"
count="$(wc -l <"$tmp_path" | tr -d '[:space:]')"
python3 - "$report_path" "$count" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "name": "rclone readonly",
    "status": "PASS",
    "detail": "gdrive remote is accessible",
    "item_count": int(sys.argv[2]),
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

