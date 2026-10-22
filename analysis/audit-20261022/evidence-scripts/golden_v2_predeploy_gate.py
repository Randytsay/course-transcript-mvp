"""Pre-deploy gate for the Golden V2 chat stack. Read-only except the backup.

Verifies: no active lease / no in-flight paid stage; disk headroom for the
ARM64 build; a fresh verified backup of the Golden V2 ledger; and that the
release compose renders for the intended services.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHA = "17d33faf6239ca5a1fcdfe91f18cef202f271b0f"
REL = Path(f"/opt/course-transcript-v2-chat/releases/{SHA}")
DATA = Path("/opt/course-transcript-v2-chat/data")
LEDGER = DATA / "golden-v2" / "ledger.sqlite"
BACKUP_ROOT = Path("/opt/course-transcript-v2-chat/backups")
results = {}


def ok(name, value, detail=""):
    results[name] = value
    print(f"{name}: {value}" + (f"  ({detail})" if detail else ""))


# --- 1. no active lease, no in-flight paid stage -------------------------
db = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
now = datetime.now(timezone.utc).isoformat()
leases = db.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%lease%'"
).fetchall()
lease_rows = []
for table in leases:
    lease_rows += db.execute(
        f"SELECT * FROM {table[0]}").fetchall()
busy = db.execute(
    "SELECT job_id,name,state FROM v2_stages WHERE state IN ('RUNNING','OUTCOME_UNKNOWN')"
).fetchall()
db.close()
ok("lease_tables", [t[0] for t in leases])
ok("active_leases", len(lease_rows))
ok("in_flight_paid_stages", len(busy))
if lease_rows or busy:
    print("GATE_STOP: active lease or in-flight paid stage")
    sys.exit(1)

# --- 2. disk headroom ----------------------------------------------------
usage = shutil.disk_usage("/")
free_gb = usage.free / 1024 ** 3
ok("disk_free_gb", round(free_gb, 1))
if free_gb < 6:
    print("GATE_STOP: insufficient disk for ARM64 image build")
    sys.exit(1)

# --- 3. verified backup of the ledger ------------------------------------
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_dir = BACKUP_ROOT / f"pre-{SHA[:12]}-{stamp}"
backup_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(LEDGER, backup_dir / "ledger.sqlite")
shutil.copy2(DATA / "golden-v2" / "ledger.sqlite-wal", backup_dir / "ledger.sqlite-wal") \
    if (DATA / "golden-v2" / "ledger.sqlite-wal").exists() else None
digest = hashlib.sha256((backup_dir / "ledger.sqlite").read_bytes()).hexdigest()
size = (backup_dir / "ledger.sqlite").stat().st_size
(backup_dir / "ledger.sqlite.sha256").write_text(digest + "\n")
# a backup is only a backup if it opens and its contents are readable
check = sqlite3.connect(f"file:{backup_dir / 'ledger.sqlite'}?mode=ro", uri=True)
integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
job_count = check.execute("SELECT COUNT(*) FROM v2_jobs").fetchone()[0]
stage_count = check.execute("SELECT COUNT(*) FROM v2_stages").fetchone()[0]
check.close()
ok("backup_path", str(backup_dir))
ok("backup_size_bytes", size)
ok("backup_sha256", digest[:16] + "...")
ok("backup_integrity_check", integrity)
ok("backup_job_rows", job_count)
ok("backup_stage_rows", stage_count)
if integrity != "ok" or size <= 0:
    print("GATE_STOP: backup failed verification")
    sys.exit(1)

# --- 4. compose render for the intended services --------------------------
env = Path("/home/ubuntu/.env")
ok("env_file_present", env.is_file())
cmd = [
    "sudo", "-E", "docker", "compose",
    "--project-name", "golden-v2-chat",
    "--project-directory", str(REL / "deploy"),
    "--env-file", str(env),
    "-f", str(REL / "deploy" / "golden-v2-chat-compose.yml"),
    "-f", str(REL / "deploy" / "golden-v2-paid-compose.yml"),
    "config", "--quiet",
]
render = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
ok("compose_render_exit", render.returncode)
if render.returncode != 0:
    print("compose stderr tail:")
    print((render.stderr or render.stdout)[-2500:])
    sys.exit(1)

Path("/srv/ai-workspace/tmp/golden-v2-deploy-gate.json").write_text(
    json.dumps({"sha": SHA, "results": results, "backup_dir": str(backup_dir)},
               ensure_ascii=False, indent=2))
print("\nGATE_PASS")
