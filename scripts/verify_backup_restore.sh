#!/usr/bin/env bash
# One-shot restore verification: downloads latest DB+secrets from Drive,
# confirms integrity (sha256 match, gpg decrypt OK, integrity_check OK),
# then cleans up. Non-destructive — does NOT overwrite live db.
set -Eeuo pipefail

DEST_DB="gdrive:13. VPS/course-transcript-mvp_backup/db"
DEST_SEC="gdrive:13. VPS/course-transcript-mvp_backup/secrets-enc"
TMP="$(mktemp -d /tmp/backup-verify.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

echo "=== latest DB backup ==="
LATEST_DB="$(rclone lsf "$DEST_DB/" | grep -v '\.sha256$' | sort | tail -1)"
[[ -z "$LATEST_DB" ]] && { echo "FAIL: no DB backups found"; exit 1; }
echo "  file: $LATEST_DB"

echo
echo "=== latest secrets backup ==="
LATEST_SEC="$(rclone lsf "$DEST_SEC/" | sort | tail -1)"
[[ -z "$LATEST_SEC" ]] && { echo "FAIL: no secrets backups found"; exit 1; }
echo "  file: $LATEST_SEC"

echo
echo "=== verify DB sha256 ==="
rclone copy "${DEST_DB}/${LATEST_DB}"           "$TMP/"
rclone copy "${DEST_DB}/${LATEST_DB}.sha256"    "$TMP/"
EXPECTED="$(cat "$TMP/${LATEST_DB}.sha256")"
ACTUAL="$(sha256sum "$TMP/$LATEST_DB" | awk '{print $1}')"
if [[ "$EXPECTED" == "$ACTUAL" ]]; then
  echo "  PASS (sha256 match)"
else
  echo "  FAIL expected=$EXPECTED actual=$ACTUAL"; exit 1
fi

echo
echo "=== verify SQLite integrity ==="
python3 - "$TMP/$LATEST_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
row = c.execute("PRAGMA integrity_check").fetchone()
c.close()
if row and row[0] == "ok":
    print("  PASS (integrity_check ok)")
else:
    raise SystemExit(f"FAIL integrity_check: {row}")
PY

echo
echo "=== verify secrets decrypt + tar contents ==="
rclone copy "${DEST_SEC}/${LATEST_SEC}" "$TMP/"
gpg --batch --passphrase-file /root/.backup-passphrase \
    -d "$TMP/$LATEST_SEC" > "$TMP/secrets.tar.gz"
echo "  decrypted: $(wc -c < "$TMP/secrets.tar.gz") bytes"
echo "  files inside:"
tar tzf "$TMP/secrets.tar.gz" | head -10
tar tzf "$TMP/secrets.tar.gz" | wc -l | awk '{print "  total entries:", $1}'

echo
echo "=== ALL PASS ==="
echo "Last DB backup:      $(rclone lsf --format "ts" "${DEST_DB}/${LATEST_DB}")"
echo "Last secrets backup: $(rclone lsf --format "ts" "${DEST_SEC}/${LATEST_SEC}")"