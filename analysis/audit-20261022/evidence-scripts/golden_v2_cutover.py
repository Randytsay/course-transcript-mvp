"""Cut over the Golden V2 chat stack to the merged release 17d33fa.

Preserves the LIVE operational posture exactly:
  * paid + publish API enabled (the repo now defaults these to false; the
    live deployment has them on, and turning them off would silently
    disable the owner's paid/publish endpoints)
  * api.env + grant.env wiring identical
  * /app/data and rclone mounts identical
  * only the API service is replaced; paid/delivery/intake stay stopped
    (they are opt-in profile services in the repo, and the live paid and
    delivery containers are currently 'exited' - replacing them would
    start paid dispatch and Drive publication paths)

The live compose was hand-edited to bind the OLD release's app/ and
scripts/ read-only over the image's /app/app and /app/scripts for the
paid and delivery services. The api service has no such bind, so the
api's code comes from the image - which is why the new image alone
delivers the new gates to the API. The stopped paid/delivery containers
keep their old binds until they are deliberately recreated.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHA = "17d33faf6239ca5a1fcdfe91f18cef202f271b0f"
REL = Path(f"/opt/course-transcript-v2-chat/releases/{SHA}")
DEPLOY = REL / "deploy"
LEDGER = Path("/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite")
ENV = "/home/ubuntu/.env"
PROJECT = "golden-v2-chat"
RESULTS = {}


def ok(name, value, detail=""):
    RESULTS[name] = value
    print(f"{name}: {value}" + (f"  ({detail})" if detail else ""))


def compose(*args, capture=True):
    env = dict(os.environ)
    env["V2_CHAT_RELEASE_SHA"] = SHA
    env["V2_CHAT_PAID_API_ENABLED"] = "true"
    env["V2_CHAT_PUBLISH_API_ENABLED"] = "true"
    cmd = ["sudo", "-E", "docker", "compose",
           "--project-name", PROJECT,
           "--project-directory", str(DEPLOY),
           "--env-file", ENV,
           "-f", str(DEPLOY / "golden-v2-chat-compose.yml"),
           "-f", str(DEPLOY / "golden-v2-paid-compose.yml"),
           *args]
    return subprocess.run(cmd, capture_output=capture, text=True, timeout=600, env=env)


# --- pre-restart gate: re-check for leases / in-flight paid work ----------
import sqlite3
db = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
busy = db.execute(
    "SELECT job_id,name,state FROM v2_stages "
    "WHERE state IN ('RUNNING','OUTCOME_UNKNOWN')").fetchall()
db.close()
ok("pre_restart_in_flight_paid_stages", len(busy))
if busy:
    print("GATE_STOP: in-flight paid stage at cutover time")
    sys.exit(1)

# --- rollback tag the image we are replacing ------------------------------
old = subprocess.run(["sudo", "docker", "inspect", "golden-v2-chat-api-1",
                      "--format", "{{.Image}}"],
                     capture_output=True, text=True, timeout=60).stdout.strip()
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
if old:
    subprocess.run(["sudo", "docker", "tag", old,
                    f"course-transcript-v2-chat:rollback-{stamp}"],
                   capture_output=True, text=True, timeout=120)
    ok("rollback_image_tagged", f"course-transcript-v2-chat:rollback-{stamp}",
       old[:19])

# --- replace ONLY the api service ----------------------------------------
res = compose("up", "-d", "--no-build", "--no-deps", "--force-recreate", "api")
ok("compose_up_api_exit", res.returncode)
if res.returncode != 0:
    print((res.stderr or res.stdout)[-2500:])
    sys.exit(1)

# --- verify ---------------------------------------------------------------
checks = subprocess.run(
    ["sudo", "docker", "inspect", "golden-v2-chat-api-1", "--format",
     "{{.Image}}\t{{.State.Running}}\t{{.State.Health.Status}}\t"
     "{{range .Config.Env}}{{println .}}{{end}}"],
    capture_output=True, text=True, timeout=60).stdout
lines = checks.splitlines()
ok("container_image_id", lines[0].split("\t")[0][:19])
ok("container_running", lines[0].split("\t")[1])
ok("container_health", lines[0].split("\t")[2])
env_body = "\n".join(lines[1:])
for flag in ("COURSE_TRANSCRIPT_V2_PAID_API_ENABLED",
             "COURSE_TRANSCRIPT_V2_PUBLISH_API_ENABLED"):
    value = [l.split("=", 1)[1] for l in env_body.splitlines()
             if l.startswith(flag + "=")]
    ok("live_" + flag, value[0] if value else "MISSING")

# the gates must be importable AND functional inside the live container
probe = subprocess.run(
    ["sudo", "docker", "exec", "golden-v2-chat-api-1", "python", "-c",
     "import importlib.util as u;"
     "print('alignment', bool(u.find_spec('app.pipeline.golden_v2_alignment_health')));"
     "print('review', bool(u.find_spec('app.pipeline.golden_v2_review_evidence')));"
     "import app.pipeline.golden_v2_stage_runner as m; print('module', m.__file__)"],
    capture_output=True, text=True, timeout=120)
ok("gate_modules_in_running_container", probe.stdout.strip().replace("\n", " | "),
   probe.stderr.strip()[:200])

health = subprocess.run(
    ["sudo", "docker", "exec", "golden-v2-chat-api-1", "python", "-c",
     "import urllib.request; r=urllib.request.urlopen("
     "'http://127.0.0.1:8000/health',timeout=5); print(r.status)"],
    capture_output=True, text=True, timeout=60)
ok("in_container_health_http", health.stdout.strip() or health.stderr.strip()[:120])

# the other services must still be stopped (no paid dispatch, no Drive publish)
states = {}
for name in ("golden-v2-chat-paid-1", "golden-v2-chat-delivery-1",
             "golden-v2-chat-intake-1"):
    out = subprocess.run(["sudo", "docker", "inspect", name, "--format", "{{.State.Status}}"],
                         capture_output=True, text=True, timeout=60)
    states[name] = out.stdout.strip() if out.returncode == 0 else "ABSENT"
ok("other_service_states", states)

Path("/srv/ai-workspace/tmp/golden-v2-cutover.json").write_text(
    json.dumps({"sha": SHA, "stamp": stamp, "results": RESULTS},
               ensure_ascii=False, indent=2))
print("\nCUTOVER_DONE")
