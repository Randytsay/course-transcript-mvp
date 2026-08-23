# course-transcript-mvp — VPS Backup & Disaster Recovery SOP

> **Purpose:** Make the VPS replaceable in ~30 minutes if it dies.
> **Strategy:** Three-tier backup (Daily DB / Encrypted secrets / Git-tracked SOP) → Google Drive `13. VPS/course-transcript-mvp_backup/`.
> **Last updated:** 2026-08-23

---

## 1. What This Repo Owns vs. What's in the Cloud

| Layer | Where | Survives VPS death? |
|---|---|---|
| Source code | GitHub `Randytsay/course-transcript-mvp` | ✅ |
| Public ingress | Cloudflare Tunnel + `ccwu.cc` DNS | ✅ |
| Paid AI compute | GCP project `course-transcript-mvp` (Gemini / Chirp / GCS) | ✅ |
| Source audio / outputs | Google Drive (rclone'd) | ✅ |
| **Production database** (SQLite jobs / delivery state) | `/opt/course-transcript-source/data/course-transcript.db` | ❌ — **must back up** |
| **Secrets** (GCP SA / billing SA / Minimax API key / Cloudflare tunnel token) | `/opt/course-transcript/secrets/` + `/home/ubuntu/.env` | ❌ — **must back up** |
| **rclone config** | `/home/ubuntu/.config/rclone/rclone.conf` | ❌ — **must back up** |
| **In-flight jobs** (Chirp batches in flight) | Docker container in-memory state | ❌ — accepted loss |

---

## 2. Backup Layout (Google Drive)

Destination: `gdrive:13. VPS/course-transcript-mvp_backup/`

```
13. VPS/course-transcript-mvp_backup/
├── db/                 # Daily SQLite snapshot (last 30 days retained)
├── secrets-enc/        # Symmetric-encrypted tarballs (.gpg) of secrets+env+rclone
│                        #   Filename convention: secrets-YYYYMMDD-HHMM.tar.gz.gpg
│                        #   Passphrase stored in Bitwarden/1Password, never on VPS
└── evidence/           # Deploy logs + this SOP + bootstrap script
```

---

## 3. What Gets Backed Up, When, How

### 3.1 Database — Daily at 03:00 UTC (≈ 11:00 Taipei)

**Cron job** (`crontab -e`):

```cron
0 3 * * * /opt/course-transcript-source/scripts/backup_db.sh
```

**Script** (`/opt/course-transcript-source/scripts/backup_db.sh`):

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

DB_PATH="/opt/course-transcript-source/data/course-transcript.db"
DEST="gdrive:13. VPS/course-transcript-mvp_backup/db"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
TMP="/tmp/course-transcript-${STAMP}.sqlite3"

# SQLite online backup (safe while db is being written)
sudo sqlite3 "$DB_PATH" ".backup '$TMP'"
chmod 600 "$TMP"

# Compute sha256 for verification
sha256sum "$TMP" | awk '{print $1}' > "${TMP}.sha256"

# Upload to Drive
rclone copy "$TMP"   "$DEST/" -P
rclone copy "${TMP}.sha256" "$DEST/" -P

# Local cleanup
rm -f "$TMP" "${TMP}.sha256"

# Retain only last 30 days on Drive
rclone delete "$DEST" --min-age 30d --drive-use-trash=false
```

**Recovery size:** usually <50 MB compressed; full restore <2 minutes.

### 3.2 Secrets — Weekly Sunday 04:00 UTC, or after any secret change

**Cron job**:

```cron
0 4 * * 0 /opt/course-transcript-source/scripts/backup_secrets.sh
```

**Script** (`/opt/course-transcript-source/scripts/backup_secrets.sh`):

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

STAMP="$(date -u +%Y%m%d-%H%M%S)"
TMP="/tmp/secrets-${STAMP}"
DEST="gdrive:13. VPS/course-transcript-mvp_backup/secrets-enc"
PASSPHRASE_FILE="/root/.backup-passphrase"

# Fail-closed: passphrase file must exist with mode 400
[[ -f "$PASSPHRASE_FILE" ]] || { echo "FATAL: $PASSPHRASE_FILE missing"; exit 1; }
[[ "$(stat -c %a "$PASSPHRASE_FILE")" == "400" ]] || { echo "FATAL: $PASSPHRASE_FILE mode is not 400"; exit 1; }

# Bundle
mkdir -p "$TMP"
sudo tar czf "${TMP}.tar.gz" \
  /opt/course-transcript/secrets/ \
  /home/ubuntu/.env \
  /home/ubuntu/.config/rclone/rclone.conf \
  /root/.backup-passphrase
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
```

### 3.3 Source Code — Already in Git

Source code is in `Randytsay/course-transcript-mvp` on GitHub. No additional backup needed.

### 3.4 Evidence / Logs — Manual or after each deploy

After each deploy, run:

```bash
rclone copy /opt/course-transcript-backups/ gdrive:13. VPS/course-transcript-mvp_backup/evidence/ -P
```

---

## 4. Manual Backup Trigger (Ad-hoc)

```bash
# Force a backup now
/opt/course-transcript-source/scripts/backup_db.sh
/opt/course-transcript-source/scripts/backup_secrets.sh

# Verify last backup exists and is recent
rclone ls gdrive:13. VPS/course-transcript-mvp_backup/db/ | tail -5
rclone ls gdrive:13. VPS/course-transcript-mvp_backup/secrets-enc/ | tail -5
```

---

## 5. Disaster Recovery — VPS Re-provisioning

If VPS dies, follow this checklist to bring it back online in ~30 minutes.

### Phase A — Provision new VPS (10 min)

1. Create a new Oracle Cloud / AWS / Hetzner VPS with the same OS (Ubuntu 24.04).
2. SSH in, update packages, install Docker:

   ```bash
   sudo apt update -qq && sudo apt install -y -qq docker.io docker-compose-plugin git curl ufw
   sudo ufw allow 22/tcp && sudo ufw enable
   ```

3. Create working directories:

   ```bash
   sudo mkdir -p /opt && sudo chown $USER:$USER /opt
   ```

### Phase B — Restore code (5 min)

```bash
cd /opt
git clone https://github.com/Randytsay/course-transcript-mvp.git course-transcript-source
cd course-transcript-source
# Restore the exact release worktree that matches production
sudo install -d -m 755 /opt/course-transcript-releases
# Use the SHA printed in evidence/last-deploy.txt (from the most recent evidence backup)
git -c "SOURCE_SHA=<paste from evidence/last-deploy.txt>" archive "$SOURCE_SHA" \
  | tar -x -C /opt/course-transcript-releases/"$SOURCE_SHA"
```

### Phase C — Restore secrets (5 min)

```bash
# 1. List recent secret backups
rclone lsf gdrive:13. VPS/course-transcript-mvp_backup/secrets-enc/ | tail -5

# 2. Download the most recent
PASSPHRASE_FROM_BITWARDEN="<paste from password manager>"
rclone copy gdrive:13. VPS/course-transcript-mvp_backup/secrets-enc/secrets-LATEST.tar.gz.gpg /tmp/

# 3. Decrypt
gpg --batch --passphrase "$PASSPHRASE_FROM_BITWARDEN" \
    -d /tmp/secrets-LATEST.tar.gz.gpg > /tmp/secrets-LATEST.tar.gz

# 4. Restore to their canonical locations
sudo mkdir -p /opt/course-transcript/secrets
sudo tar xzf /tmp/secrets-LATEST.tar.gz -C /
# .env ends up at /home/ubuntu/.env (or wherever the path matched)

# 5. Cleanup
rm -f /tmp/secrets-LATEST.tar.gz /tmp/secrets-LATEST.tar.gz.gpg
```

### Phase D — Restore database (2 min)

```bash
# 1. Find latest backup
rclone lsf gdrive:13. VPS/course-transcript-mvp_backup/db/ | sort | tail -5

# 2. Download latest
rclone copy gdrive:13. VPS/course-transcript-mvp_backup/db/course-transcript-LATEST.sqlite3 /tmp/

# 3. Verify integrity
sha256sum /tmp/course-transcript-LATEST.sqlite3
# Compare against the .sha256 sidecar (also in Drive) — must match

# 4. Place at the canonical path
sudo mkdir -p /opt/course-transcript-source/data
sudo cp /tmp/course-transcript-LATEST.sqlite3 /opt/course-transcript-source/data/
sudo chown ubuntu:ubuntu /opt/course-transcript-source/data/course-transcript.db
sudo chmod 640 /opt/course-transcript-source/data/course-transcript.db

# 5. Verify
sudo sqlite3 /opt/course-transcript-source/data/course-transcript.db "PRAGMA integrity_check;"
# Expected: ok
```

### Phase E — Bring services up (5 min)

```bash
cd /opt/course-transcript-source

# 1. Validate compose
sudo docker compose --env-file /home/ubuntu/.env --profile web config --quiet

# 2. Start everything
sudo docker compose --env-file /home/ubuntu/.env --profile web up -d
sudo docker compose --env-file /home/ubuntu/.env --profile billing up -d
sudo docker compose --env-file /home/ubuntu/.env --profile tunnel up -d

# 3. Wait for healthcheck
sleep 30
sudo docker ps --format '{{.Names}} {{.State.Status}}'
```

### Phase F — Cloudflare tunnel re-attachment (3 min)

1. Log in to Cloudflare dashboard → `ccwu.cc` → Zero Trust → Networks → Tunnels.
2. Find the existing tunnel; do NOT create a new one (same tunnel ID keeps DNS working).
3. Install the new connector on the new VPS — either:
   - **Same tunnel token**: Copy token from a previously-saved secret backup, run `cloudflared service install <token>`. OR
   - **New connector for existing tunnel**: Dashboard → Tunnel → "Add an instance" → copy the install command, run on new VPS.

### Phase G — Verify (5 min)

```bash
# Local health
curl http://127.0.0.1:3300/api/v1/health   # frontend
curl http://127.0.0.1:8000/api/v1/health   # api

# Public health
curl -I https://transcript.randy88.ccwu.cc/
curl -I https://review.randy88.ccwu.cc/review

# Rclone a test file
echo "test" | rclone copy - gdrive:13. VPS/course-transcript-mvp_backup/evidence/test.txt
rclone delete gdrive:13. VPS/course-transcript-mvp_backup/evidence/test.txt
```

If all green, declare restore complete.

---

## 6. Recovery Time Estimates

| Phase | Time |
|---|---|
| A. Provision new VPS | 10 min |
| B. Restore code | 5 min |
| C. Restore secrets | 5 min |
| D. Restore database | 2 min |
| E. Bring services up | 5 min |
| F. Re-attach Cloudflare tunnel | 3 min |
| G. Verify | 5 min |
| **Total** | **~35 min** |

---

## 7. What Will NOT Survive

- **In-flight Chirp batches** — they live in the pipeline worker's in-memory state. At worst, jobs marked `running` will need manual re-marking as `failed` and re-submission.
- **Local `logs/` directory** — temporary; valuable for debugging but not state-critical.
- **Docker images built locally** — must be rebuilt from the restored source.

---

## 8. Maintenance

### 8.1 Quarterly verification

Run this to confirm backups actually restore:

```bash
# 1. Verify last DB backup is < 24h old
rclone lsf gdrive:13. VPS/course-transcript-mvp_backup/db/ --format "tp" | sort | tail -3

# 2. Verify last secrets backup is < 8 days old
rclone lsf gdrive:13. VPS/course-transcript-mvp_backup/secrets-enc/ --format "tp" | sort | tail -3

# 3. Spot-restore test (optional, manual):
#    Download latest DB, open in sqlite, verify it has expected tables
```

### 8.2 When secrets change

After rotating any secret in `/opt/course-transcript/secrets/`:

```bash
sudo /opt/course-transcript-source/scripts/backup_secrets.sh
```

### 8.3 When production SHA changes (after deploy)

After `deploy_release.sh` runs, the new SHA should be noted in `evidence/last-deploy.txt`:

```bash
echo "$(date -u +%Y%m%d-%H%M%S) $(sudo docker inspect course-transcript-source-api-1 \
  --format='{{index .Config.Labels "org.opencontainers.image.revision"}}')" \
  | rclone rcat gdrive:13. VPS/course-transcript-mvp_backup/evidence/last-deploy.txt
```

---

## 9. Open Questions / Known Gaps

- **GCS buckets** are not backed up locally. They are owned by the GCP project and survive VPS death automatically, but if the GCP project itself is lost, Chirp batch history is gone. Mitigation: project-level backups out of scope for this SOP.
- **Production `.env`** is included in secrets-enc. If rotation changes the structure, the encrypted bundle stays backward-compatible as long as the passphrase does not change.
- **Owner personal Drive quota** (5 TB; current usage ~1.9 TB; recovery room ~3 TB) is the hard ceiling. Daily DB growth is ~MB-scale; secrets are <5 MB; comfortable headroom for years.
