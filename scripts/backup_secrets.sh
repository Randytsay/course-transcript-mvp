#!/usr/bin/env bash
# Weekly secrets + .env + rclone config → symmetric-encrypted gdrive.
# Cron: 0 4 * * 0 /opt/course-transcript-source/scripts/backup_secrets.sh
set -Eeuo pipefail

DEST="gdrive:13. VPS/course-transcript-mvp_backup/secrets-enc"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
TMP="/tmp/secrets-${STAMP}"
PASSPHRASE_FILE="/root/.backup-passphrase"
LOG="/var/log/backup_secrets.log"

mkdir -p "$(dirname "$LOG")"
exec >> "$LOG" 2>&1
echo "=== backup_secrets $STAMP ==="

# Fail-closed: passphrase file must exist with mode 400
[[ -f "$PASSPHRASE_FILE" ]] || { echo "FATAL: $PASSPHRASE_FILE missing"; exit 1; }
[[ "$(stat -c %a "$PASSPHRASE_FILE")" == "400" ]] || { echo "FATAL: $PASSPHRASE_FILE mode is not 400"; exit 1; }

# Bundle
mkdir -p "$TMP"
sudo tar czf "${TMP}.tar.gz" \
  /opt/course-transcript/secrets/ \
  /home/ubuntu/.env \
  /home/ubuntu/.config/rclone/rclone.conf \
  "${PASSPHRASE_FILE}"
chmod 600 "${TMP}.tar.gz"

# Symmetric encrypt with passphrase file (NOT interactive)
gpg --batch --yes --passphrase-file "$PASSPHRASE_FILE" \
    --cipher-algo AES256 --symmetric --output "${TMP}.tar.gz.gpg" "${TMP}.tar.gz"

# Upload
rclone copy "${TMP}.tar.gz.gpg" "$DEST/" -P

# Cleanup local plaintext + ciphertext (VPS keeps nothing)
rm -rf "$TMP" "${TMP}.tar.gz" "${TMP}.tar.gz.gpg"

# Retain last 8 weekly backups (~2 months)
rclone delete "$DEST" --min-age 60d --drive-use-trash=false

echo "=== backup_secrets $STAMP DONE ==="
