#!/usr/bin/env bash
# Daily SQLite online backup → encrypted gdrive.
# Cron: 0 3 * * * /opt/course-transcript-source/scripts/backup_db.sh
set -Eeuo pipefail

DB_PATH="/opt/course-transcript-source/data/course-transcript.db"
DEST="gdrive:13. VPS/course-transcript-mvp_backup/db"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
TMP="/tmp/course-transcript-${STAMP}.sqlite3"
LOG="/var/log/backup_db.log"

mkdir -p "$(dirname "$LOG")"
exec >> "$LOG" 2>&1
echo "=== backup_db $STAMP ==="

# Sanity check DB exists
[[ -f "$DB_PATH" ]] || { echo "FATAL: DB not found at $DB_PATH"; exit 1; }

# Use Python's sqlite3 (always present; works without root PATH issues).
# This is an online backup safe while the live db is being written.
python3 - "$DB_PATH" "$TMP" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
    row = dst.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        raise SystemExit(f"integrity_check failed: {row}")
finally:
    dst.close()
    src.close()
PY
chmod 600 "$TMP"

# Compute sha256 for verification
sha256sum "$TMP" | awk '{print $1}' > "${TMP}.sha256"

# Upload to Drive
rclone copy "$TMP"          "$DEST/" -P
rclone copy "${TMP}.sha256" "$DEST/" -P

# Local cleanup
rm -f "$TMP" "${TMP}.sha256"

# Retain only last 30 days on Drive
rclone delete "$DEST" --min-age 30d --drive-use-trash=false

echo "=== backup_db $STAMP DONE ==="
