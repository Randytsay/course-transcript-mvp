"""Fail-closed Golden finalization for Market America course transcripts.

This script intentionally does *not* equate "pipeline completed" with
"semantic Golden".  A job needs durable evidence for every gate, including
material inventory and semantic review/correction.  Existing provider evidence
is read only; the script never starts Chirp/Gemini and never mutates Drive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _material_evidence(
    *,
    job_dir: Path,
    static_job: dict[str, Any],
    quality_sha256: str | None,
    persist: bool,
) -> tuple[dict[str, Any], bool]:
    materials = static_job.get("course_materials")
    if not isinstance(materials, dict):
        materials = None
    existing = load(job_dir / "material-evidence.json", {})

    counts = {
        key: int((materials or {}).get(key) or 0)
        for key in ("image_count", "slide_count", "text_count", "timestamped_image_count")
    }
    items = (materials or {}).get("items")
    if not isinstance(items, list):
        items = []

    complete = bool(
        materials is not None
        and quality_sha256
        and isinstance(static_job.get("lineage_relations"), list)
    )
    evidence = {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "course_key": static_job.get("course_key"),
        "inventory_status": "COMPLETE" if complete else "INCOMPLETE",
        "source_report_sha256": quality_sha256,
        "summary": counts,
        "items": items,
        "lineage_relations": static_job.get("lineage_relations") or [],
        "prior_evidence_sha256": _sha256(job_dir / "material-evidence.json"),
        "policy": (
            "A complete zero-item inventory is valid. Inventory completeness means the "
            "course was scanned and recorded; it does not mean material text was used "
            "to rewrite unsupported audio."
        ),
    }
    if complete and existing.get("inventory_status") == "COMPLETE":
        evidence["prior_inventory_complete"] = True
    if persist:
        write(job_dir / "material-evidence.json", evidence)
    return evidence, complete


def _semantic_evidence(job_dir: Path) -> dict[str, Any]:
    """Return a fail-closed semantic-review verdict.

    Accepted evidence is either an actual ChatGPT handoff import audit or an
    explicit semantic-review.json produced by a reviewer.  Deterministic
    cleanup, terminology snapshots, and a QA report with no structural errors
    are necessary supporting evidence but are not semantic correction by
    themselves.
    """

    import_audit_path = job_dir / "chatgpt-handoff" / "import-audit.json"
    if import_audit_path.is_file():
        audit = load(import_audit_path, {})
        cue_count = audit.get("cue_count")
        if isinstance(audit, dict) and isinstance(cue_count, int) and cue_count > 0:
            return {
                "status": "PASS",
                "mode": "chatgpt_handoff_import",
                "evidence_path": "chatgpt-handoff/import-audit.json",
                "evidence_sha256": _sha256(import_audit_path),
                "cue_count": cue_count,
            }

    review_path = job_dir / "semantic-review.json"
    if review_path.is_file():
        review = load(review_path, {})
        reviewer = str(review.get("reviewer") or "").strip()
        scope = str(review.get("review_scope") or "").strip()
        if (
            review.get("status") == "PASS"
            and reviewer
            and reviewer.lower() not in {"automatic", "system", "unknown"}
            and scope in {"full_text", "full_text_or_high_risk_candidates"}
            and int(review.get("unresolved_blocker_count") or 0) == 0
        ):
            return {
                "status": "PASS",
                "mode": "semantic_review",
                "reviewer": reviewer,
                "review_scope": scope,
                "evidence_path": "semantic-review.json",
                "evidence_sha256": _sha256(review_path),
                "changed_segment_count": int(review.get("changed_segment_count") or 0),
                "unresolved_blocker_count": int(review.get("unresolved_blocker_count") or 0),
            }

    return {
        "status": "REVIEW_REQUIRED",
        "mode": "none",
        "reason": (
            "No durable ChatGPT import or explicit semantic-review PASS exists. "
            "Deterministic cleanup/terminology evidence alone cannot certify semantic Golden quality."
        ),
    }


def _qa_ok(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("errors"), list) and not payload.get("errors")


def _publication_ok(job_dir: Path, publication: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Require the Drive state to match the current semantic display artifacts."""
    if publication.get("status") != "completed":
        return False, {"status": publication.get("status"), "reason": "publication_not_completed"}

    files = publication.get("files") if isinstance(publication.get("files"), dict) else {}
    expected: dict[str, tuple[Path, str]] = {}
    srt = job_dir / "subtitles-cleaned.srt"
    if srt.is_file():
        expected["srt"] = (srt, _sha256(srt) or "")
    txt = job_dir / "transcript-cleaned.txt"
    if txt.is_file():
        expected["txt"] = (txt, _sha256(txt) or "")

    if not expected:
        return False, {"status": publication.get("status"), "reason": "missing_semantic_display_artifacts"}

    mismatches = []
    checked = {}
    for fmt, (path, digest) in expected.items():
        record = files.get(fmt) if isinstance(files.get(fmt), dict) else {}
        published_sha = str(record.get("sha256") or "")
        fresh = bool(record.get("status") == "completed" and digest and published_sha == digest)
        checked[fmt] = {
            "local_name": path.name,
            "local_sha256": digest,
            "published_sha256": published_sha or None,
            "fresh": fresh,
        }
        if not fresh:
            mismatches.append(fmt)
    return not mismatches, {
        "status": publication.get("status"),
        "checked": checked,
        "stale_or_missing_formats": mismatches,
    }


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    data = Path(args.data_dir)
    quality_path = Path(args.quality_report)
    quality = load(quality_path, {})
    quality_sha256 = _sha256(quality_path)
    static = {
        str(item.get("id")): item
        for item in quality.get("jobs", [])
        if isinstance(item, dict) and item.get("id")
    }

    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    jobs = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM jobs WHERE batch_id=? ORDER BY queue_position",
            (args.batch_id,),
        )
    ]

    provisional: list[dict[str, Any]] = []
    for job in jobs:
        job_dir = data / "jobs" / str(job["id"])
        static_job = static.get(str(job["id"]), {})
        material, material_ok = _material_evidence(
            job_dir=job_dir,
            static_job=static_job,
            quality_sha256=quality_sha256,
            persist=bool(getattr(args, "write_job_evidence", False)),
        )
        coverage = load(job_dir / "chirp-completeness.json", {})
        canonical = load(job_dir / "canonical-coverage.json", {})
        terminology = load(job_dir / "ma-terminology-gate.json", {})
        qa = load(job_dir / "qa-report.json", {})
        golden_publication_path = job_dir / "golden-drive-publish-state.json"
        publication = load(
            golden_publication_path,
            load(job_dir / "drive-publish-state.json", {}),
        )
        semantic = _semantic_evidence(job_dir)
        publication_fresh, publication_evidence = _publication_ok(job_dir, publication)

        canonical_job_id = (
            str(canonical.get("canonical_job_id") or "")
            if canonical.get("disposition") == "covered_by_canonical"
            else ""
        )
        checks = {
            "job_completed": job.get("status") == "completed",
            "coverage_pass": coverage.get("status") == "PASS",
            "canonical_coverage": bool(canonical_job_id),
            "qa_no_errors": _qa_ok(qa),
            "terminology_gate_pass": terminology.get("status") == "PASS",
            "material_inventory_complete": material_ok,
            "semantic_review_pass": semantic.get("status") == "PASS",
            "drive_publish_complete": publication.get("status") == "completed",
            "drive_publish_fresh": publication_fresh,
        }
        inherited_checks = {"coverage_pass", "canonical_coverage"}
        if canonical_job_id:
            # A derivative/alternate covered by a canonical job must not need its
            # own semantic certification. Semantic Golden readiness is inherited
            # only after the canonical job itself passes the strict gate below.
            inherited_checks.update({"semantic_review_pass", "qa_no_errors", "drive_publish_fresh"})
        blocking = [
            name
            for name, passed in checks.items()
            if name not in inherited_checks and not passed
        ]
        if not (checks["coverage_pass"] or checks["canonical_coverage"]):
            blocking.append("coverage_or_canonical")

        provisional.append(
            {
                "schema_version": 2,
                "generated_at": datetime.now(UTC).isoformat(),
                "job_id": job["id"],
                "source_name": job["source_name"],
                "source_path": job["source_path"],
                "canonical_job_id": canonical_job_id or None,
                "checks": checks,
                "blocking_checks": blocking,
                "semantic_evidence": semantic,
                "publication_evidence": publication_evidence,
                "publication_evidence_path": (
                    "golden-drive-publish-state.json"
                    if golden_publication_path.is_file()
                    else "drive-publish-state.json"
                ),
                "material_evidence": {
                    "inventory_status": material.get("inventory_status"),
                    "summary": material.get("summary"),
                    "source_report_sha256": material.get("source_report_sha256"),
                },
            }
        )

    by_id = {str(item["job_id"]): item for item in provisional}
    results: list[dict[str, Any]] = []
    for item in provisional:
        canonical_job_id = str(item.get("canonical_job_id") or "")
        checks = item["checks"]
        blocking = list(item["blocking_checks"])
        canonical_ready = False
        if canonical_job_id:
            canonical_item = by_id.get(canonical_job_id)
            canonical_ready = bool(
                canonical_item
                and not canonical_item["blocking_checks"]
                and canonical_item["checks"].get("semantic_review_pass")
            )
            if not canonical_ready:
                blocking.append("canonical_not_semantic_golden")

        if blocking:
            status = (
                "SEMANTIC_REVIEW_REQUIRED"
                if set(blocking).issubset({"semantic_review_pass", "canonical_not_semantic_golden"})
                else "BLOCKED"
            )
        elif canonical_job_id:
            status = "COVERED_BY_CANONICAL"
        else:
            status = "GOLDEN"

        final = {
            **item,
            "status": status,
            "golden": status == "GOLDEN",
            "knowledge_ingestion_allowed": status == "GOLDEN",
            "canonical_ready": canonical_ready if canonical_job_id else None,
            "blocking_checks": sorted(set(blocking)),
        }
        if bool(getattr(args, "write_job_evidence", False)):
            write(data / "jobs" / str(item["job_id"]) / "golden-marker.json", final)
        results.append(final)

    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "batch_id": args.batch_id,
        "provider_calls_started": False,
        "drive_mutation_started": False,
        "job_evidence_mutation_started": bool(getattr(args, "write_job_evidence", False)),
        "quality_report_sha256": quality_sha256,
        "status_counts": counts,
        "golden_count": sum(item["status"] == "GOLDEN" for item in results),
        "canonical_covered_count": sum(item["status"] == "COVERED_BY_CANONICAL" for item in results),
        "semantic_review_required_count": sum(
            item["status"] == "SEMANTIC_REVIEW_REQUIRED" for item in results
        ),
        "blocked_count": sum(item["status"] == "BLOCKED" for item in results),
        "knowledge_ingestion_count": sum(bool(item["knowledge_ingestion_allowed"]) for item in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed semantic Golden finalization for Market America transcript jobs."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--quality-report", required=True)
    parser.add_argument("--database", default="/app/data/course-transcript.db")
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--write-job-evidence",
        action="store_true",
        help="Persist material-evidence/golden-marker sidecars. Default is audit-only.",
    )
    args = parser.parse_args()
    summary = build_summary(args)
    write(Path(args.output), summary)
    print(
        json.dumps(
            {
                "status_counts": summary["status_counts"],
                "golden_count": summary["golden_count"],
                "knowledge_ingestion_count": summary["knowledge_ingestion_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
