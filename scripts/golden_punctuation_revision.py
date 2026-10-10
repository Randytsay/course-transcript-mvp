"""Bounded, backup-first punctuation revision for one Golden Subtitle job.

Default is a read-only preview.  --apply is intentionally restricted to
pure punctuation repairs, zero added/removed lexical characters, immutable
segment IDs/timestamps, and a successful zero-cost QA rerun.

Does not touch Google Drive, original audio, raw Chirp words, or DB status.
Never marks semantic evidence completed or upgrades Golden QA to PASS.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

BATCH = "batch-20261009-104458-752e60"
REPLACEMENTS = (("？，", "？"), ("；，", "；"), ("。，", "。"), ("！，", "！"))
REQUIRED = ("subtitles-cleaned.json", "subtitles-cleaned.srt", "transcript-cleaned.txt")
OPTIONAL = ("transcript.srt", "transcript_corrected.txt")
QA_FILES = ("qa-report.json", "qa-report.md", "density-retry-plan.json")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def punctuation_only(text: str) -> str:
    return "".join(c for c in text if not unicodedata.category(c).startswith("P") and not c.isspace())


def stamp(ms: int) -> str:
    hr, rem = divmod(int(ms), 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, rem = divmod(rem, 1000)
    return f"{hr:02d}:{minute:02d}:{second:02d},{rem:03d}"


def outputs(document: dict) -> tuple[bytes, bytes]:
    cues = document["display_segments"]
    srt = "\n\n".join(
        f"{idx}\n{stamp(s['start_ms'])} --> {stamp(s['end_ms'])}\n{s['cleaned_text']}"
        for idx, s in enumerate(cues, 1)
    ) + "\n"
    txt = "\n".join(s["cleaned_text"] for s in cues) + "\n"
    return srt.encode("utf-8"), txt.encode("utf-8")


def preview(job_dir: Path) -> dict:
    if not all((job_dir / f).is_file() for f in REQUIRED):
        raise ValueError("Missing expected cleaned JSON, SRT, or TXT")
    if any((job_dir / f).is_symlink() for f in REQUIRED):
        raise ValueError("Symlink in canonical output; refusing mutation")
    source_bytes = (job_dir / "subtitles-cleaned.json").read_bytes()
    document = json.loads(source_bytes)
    if not isinstance(document.get("display_segments"), list) or not document["display_segments"]:
        raise ValueError("Missing display_segments")
    old_srt, old_txt = outputs(document)
    if (job_dir / "subtitles-cleaned.srt").read_bytes() != old_srt:
        raise ValueError("Existing SRT differs from canonical JSON renderer; needs separate review")
    if (job_dir / "transcript-cleaned.txt").read_bytes() != old_txt:
        raise ValueError("Existing TXT differs from canonical JSON renderer; needs separate review")
    for opt, canonical in (("transcript.srt", old_srt), ("transcript_corrected.txt", old_txt)):
        file = job_dir / opt
        if file.exists() and file.read_bytes() != canonical:
            raise ValueError(f"{opt} differs from canonical JSON; refusing to overwrite")
    updated = copy.deepcopy(document)
    changes = []
    for seg in updated["display_segments"]:
        before = seg.get("cleaned_text")
        if not isinstance(before, str):
            raise ValueError("Non-text cleaned_text segment")
        after = before
        for a, b in REPLACEMENTS:
            after = after.replace(a, b)
        if after != before:
            if punctuation_only(after) != punctuation_only(before):
                raise AssertionError("Non-punctuation lexical change")
            changes.append({"segment_id": seg["segment_id"], "before": before, "after": after})
            seg["cleaned_text"] = after
    if len(updated["display_segments"]) != len(document["display_segments"]):
        raise AssertionError("Unexpected segment count change")
    for left, right in zip(document["display_segments"], updated["display_segments"]):
        for key in ("segment_id", "start_ms", "end_ms"):
            if left[key] != right[key]:
                raise AssertionError(f"Immutable {key} altered")
    if document.get("segments") != updated.get("segments"):
        raise AssertionError("Raw Chirp segments changed")
    new_srt, new_txt = outputs(updated)
    encoded = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8")
    return {"source_sha256": digest(source_bytes), "changes": changes, "raw_segments_preserved": True,
            "timecodes_preserved": True, "new_data": {"subtitles-cleaned.json": encoded,
            "subtitles-cleaned.srt": new_srt, "transcript-cleaned.txt": new_txt,
            **({"transcript.srt": new_srt} if (job_dir / "transcript.srt").exists() else {}),
            **({"transcript_corrected.txt": new_txt} if (job_dir / "transcript_corrected.txt").exists() else {})}}


def atomic_write(path: Path, contents: bytes) -> None:
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".golden-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def apply(data_root: Path, job_id: str, expected_sha: str, qa_script: Path) -> dict:
    directory = data_root / "jobs" / job_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Unsafe job directory")
    db = sqlite3.connect(f"file:{data_root/'course-transcript.db'}?mode=ro", uri=True)
    try:
        job = db.execute("SELECT status, batch_id, locked_by FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        db.close()
    if not job or job[1] != BATCH or job[0] != "awaiting_review" or job[2]:
        raise ValueError("Job not authorized for offline punctuation revision")
    if json.loads((directory / "golden-review-provenance.json").read_text()).get("semantic_review_completed") is True:
        raise ValueError("Verified semantic provenance exists; a revision would invalidate it")
    lock_path = data_root / "golden-runner" / "runner.lock"
    with lock_path.open("ab") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        task = preview(directory)
        if task["source_sha256"] != expected_sha:
            raise ValueError("Source hash changed since review; refusing stale revision")
        if not task["changes"]:
            return {"changed": False, "job_id": job_id, "reason": "already_clean"}
        qa_before = json.loads((directory / "qa-report.json").read_text())
        if qa_before.get("status") != "REVIEW" or not qa_before.get("review_required"):
            raise ValueError("Job no longer in Golden REVIEW")
        prior = {name: (directory / name).read_bytes() for name in
                 (*task["new_data"].keys(), *QA_FILES) if (directory / name).is_file()}
        backup = directory / "backups" / (
            "pre-punctuation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        backup.mkdir(parents=True, exist_ok=False)
        manifest = {
            "job_id": job_id, "source_sha256": expected_sha,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "PREPARED", "modified_segment_ids": [x["segment_id"] for x in task["changes"]],
            "backup_sha256": {k: digest(v) for k, v in prior.items()},
        }
        for key, value in prior.items():
            atomic_write(backup / key, value)
        atomic_write(backup / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
        if any((directory / name).read_bytes() != prior[name] for name in prior):
            raise RuntimeError("Source changed after backup; refusing write")
        try:
            for name, value in task["new_data"].items():
                atomic_write(directory / name, value)
            env = dict(os.environ, COURSE_TRANSCRIPT_DATA_DIR=str(data_root), JOB_NAME=job_id)
            env["PYTHONPATH"] = str(qa_script.parent.parent.parent)
            completed = subprocess.run([sys.executable, str(qa_script)],
                env=env, capture_output=True, text=True, timeout=90)
            if completed.returncode:
                raise RuntimeError("QA script failed: " + completed.stderr[-350:])
            qa_after = json.loads((directory / "qa-report.json").read_text())
            if qa_after.get("status") == "PASS":
                # An unverified mechanical revision must never be the change
                # that silently promotes Golden QA to PASS.  A separate
                # independent semantic/acoustic stage must authorize that.
                raise RuntimeError("Mechanical-only revision cannot promote Golden QA to PASS")
            original_review = set(qa_before["review_required"])
            new_review = set(qa_after.get("review_required") or [])
            expected_removed = {
                "subtitle contains repeated punctuation: " + item["segment_id"]
                for item in task["changes"]
            }
            if original_review - new_review != expected_removed or new_review - original_review:
                raise RuntimeError("Unexpected QA issue change; rollback required")
            if qa_after.get("status") != "REVIEW" or len(new_review) >= len(original_review):
                raise RuntimeError("QA review count did not improve safely")
            manifest.update({"status": "QA_REVIEW_IMPROVED", "old_reviews": len(qa_before["review_required"]),
                             "new_reviews": len(qa_after["review_required"]),
                             "note": "Not Golden semantic PASS; no remote mutation"})
            atomic_write(backup / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
            return {"changed": True, "job_id": job_id, "backup": str(backup),
                    "repaired_segments": [x["segment_id"] for x in task["changes"]],
                    "old_reviews": len(qa_before["review_required"]),
                    "new_reviews": len(qa_after["review_required"]), "paid_calls": 0, "drive_uploads": 0}
        except Exception:
            for name, contents in prior.items():
                atomic_write(directory / name, contents)
            manifest["status"] = "ROLLED_BACK"
            atomic_write(backup / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
            raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-source-sha256", default="")
    parser.add_argument("--qa-script", type=Path, default=Path(__file__).resolve().parents[1]/"app/providers/qa_report.py")
    args = parser.parse_args()
    job_dir = args.data_root/"jobs"/args.job_id
    if not re.fullmatch(r"[a-z0-9-]{6,130}", args.job_id):
        raise ValueError("Invalid job id")
    outcome = (apply(args.data_root, args.job_id, args.expected_source_sha256, args.qa_script)
               if args.apply else preview(job_dir))
    if not args.apply:
        outcome = {key: value for key, value in outcome.items() if key != "new_data"}
        outcome["preview_only"] = True
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())