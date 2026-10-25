"""Source-bound shadow acceptance, no cloud/provider IO, for four existing held jobs."""
import json
import sqlite3
from pathlib import Path

from app.jobs.golden_v2_ledger import GoldenV2Ledger
from app.pipeline.golden_v2_ai_limited_closeout import (
    prepare_limited_manifest, register_proposal,
)

ROOT = Path("/shadow")
SOURCES = Path("/source")
SCOPE = [
    ("250503", "v2p_f8505d06c77f5789aa4bba9b461ee1f3",
     "語音 250503_原稿校對底稿_待複核_20261025.srt",
     "語音 250503_原稿校對底稿_待複核_20261025.txt"),
    ("251025", "v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be",
     "251025_術語校正版_待原音複核_20261025.srt",
     "251025_術語校正版_待原音複核_20261025.txt"),
    ("251018", "v2p_560465ab6db05e3da593837f4d76c630",
     "251018_術語校正版_待原音複核_20261025.srt",
     "251018_術語校正版_待原音複核_20261025.txt"),
    ("261017", "v2p_62a124bdd4595aca91c53085c3732ca7",
     "261017_drive_original.srt",
     "261017_drive_original.txt"),
]
if not (SOURCES / "ledger.sqlite").is_file():
    raise RuntimeError("source ledger unavailable")
if (ROOT / "ledger.sqlite").exists():
    raise RuntimeError("shadow already exists; refuse overwrite")
source = sqlite3.connect(
    "file:" + str(SOURCES / "ledger.sqlite") + "?mode=ro", uri=True
)
with sqlite3.connect(ROOT / "ledger.sqlite") as clone:
    source.backup(clone)
source.close()
ledger = GoldenV2Ledger.open_existing(ROOT / "ledger.sqlite")
for label, job, srt, txt in SCOPE:
    stage_before = ledger.status(job)
    folder = ROOT / "jobs" / job / "golden-v2"
    triage = next(folder.glob("acoustic-triage-against-best-srt-20261025-*.json"))
    name = ("語音 250503" if label == "250503" else label) + "_AI校對_含待複核_20261025"
    manifest = prepare_limited_manifest(
        root=ROOT,job_id=job,
        srt_path=folder / srt,txt_path=folder / txt,
        triage_path=triage,
        remote_names=(name+".srt",name+".txt"),
    )
    outcome = register_proposal(ROOT, manifest)
    after = ledger.status(job)
    if after["revision"] != stage_before["revision"] or after["stages"] != stage_before["stages"]:
        raise RuntimeError("shadow proposal mutated Golden stages")
    print(json.dumps({
        "source": label, "receipt_state":outcome["state"],
        "proposal_sha":outcome["proposal_sha256"],
        "cues": manifest["cue_count"],
        "open_items": len(manifest["open_items"]),
        "golden_status":after["status"],
    },ensure_ascii=False),flush=True)
with sqlite3.connect(ROOT / "ledger.sqlite") as check:
    assert check.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert check.execute("SELECT COUNT(*) FROM v2_ai_limited_deliveries WHERE state='PREPARED'").fetchone()[0]==4
print("FOUR_JOB_SHADOW_READY; 0 provider calls; 0 production ledger mutations")
