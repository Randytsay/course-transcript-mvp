from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app.providers.subtitle_cleanup as subtitle_cleanup


def _load_segments(job_dir: Path) -> tuple[str, list[dict[str, Any]]] | None:
    for name in ("subtitles-corrected.json", "subtitles.json"):
        path = job_dir / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = payload.get("segments") if isinstance(payload, dict) else None
        if isinstance(segments, list) and segments:
            return name, [item for item in segments if isinstance(item, dict)]
    return None


def audit_history(data_dir: Path) -> dict[str, Any]:
    db = data_dir / "course-transcript.db"
    if not db.is_file():
        raise FileNotFoundError(db)
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    subtitle_cleanup.DATA_DIR = data_dir
    rows = connection.execute(
        "SELECT id, source_name, status, content_mode FROM jobs "
        "WHERE content_mode='dacheng_buddhist' ORDER BY created_at, id"
    ).fetchall()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        job_dir = data_dir / "jobs" / str(row["id"])
        loaded = _load_segments(job_dir)
        if loaded is None:
            jobs.append({
                "job_id": row["id"],
                "source_name": row["source_name"],
                "status": "SKIP",
                "reason": "subtitle_source_missing",
            })
            continue
        source_name, segments = loaded
        report = subtitle_cleanup.build_report(
            source_name,
            segments,
            content_mode="dacheng_buddhist",
        )
        prior_path = job_dir / "cleanup-review.json"
        prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.is_file() else None
        jobs.append({
            "job_id": row["id"],
            "source_name": row["source_name"],
            "job_status": row["status"],
            "source_layer": source_name,
            "segment_count": len(segments),
            "new_status": report.get("status"),
            "scripture": report.get("scripture"),
            "mantra": report.get("mantra"),
            "review_count": len(report.get("review_required", [])),
            "requires_text_recorrection": True,
            "requires_paid_provider_call_for_text_recorrection": True,
            "prior_available": prior is not None,
            "changed_vs_prior": prior != report if prior is not None else None,
        })
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "dry_run_audit_only",
        "raw_provider_evidence_mutated": False,
        "job_count": len(jobs),
        "jobs": jobs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run audit of historical Dacheng lessons under the current canonical rules"
    )
    parser.add_argument("--data-dir", type=Path, default=Path("/app/data"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_history(args.data_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
