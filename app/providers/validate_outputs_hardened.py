"""Run structural validation plus severe Gemini content-drift checks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.providers import validate_outputs as base
from app.providers.correct_text_hardened import content_guard
from app.providers.hardening_common import atomic_json, iso


def _content_validation(job: Path) -> tuple[list[str], list[dict[str, Any]]]:
    corrected_path = job / "subtitles-corrected.json"
    if not corrected_path.is_file():
        return [], []
    payload = json.loads(corrected_path.read_text(encoding="utf-8"))
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    errors: list[str] = []
    fallback_segments: list[dict[str, Any]] = []
    for item in segments:
        if not isinstance(item, dict):
            errors.append("corrected subtitle contains a non-object segment")
            continue
        segment_id = str(item.get("segment_id") or "")
        raw = str(item.get("raw_text") or "")
        corrected = str(item.get("corrected_text") or item.get("text") or "")
        reasons = content_guard(raw, corrected)
        if reasons and not bool(item.get("correction_fallback")):
            errors.append(
                f"segment {segment_id} failed content guard without raw fallback: "
                + ",".join(reasons)
            )
        if bool(item.get("correction_fallback")):
            fallback_segments.append(
                {
                    "segment_id": segment_id,
                    "reasons": item.get("content_qa_reasons")
                    or item.get("fallback_reason")
                    or reasons,
                }
            )
        if not corrected.strip():
            errors.append(f"segment {segment_id} has empty published text")
    return errors, fallback_segments


def _add_report_to_export_manifest(job: Path, report_path: Path) -> None:
    manifest_path = job / "export-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return
    if not isinstance(manifest, dict):
        return
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return
    artifacts[:] = [
        item
        for item in artifacts
        if not isinstance(item, dict) or item.get("name") != report_path.name
    ]
    artifacts.append(
        {
            "name": report_path.name,
            "size_bytes": report_path.stat().st_size,
            "sha256": base.sha256(report_path),
            "source": "post-correction severe content-drift validation",
            "public": False,
            "output_format": None,
        }
    )
    atomic_json(manifest_path, manifest)


def main() -> int:
    structural = base.main()
    errors, fallbacks = _content_validation(base.JOB)
    report = {
        "generated_at": iso(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "fallback_count": len(fallbacks),
        "fallback_segments": fallbacks,
        "policy": {
            "severe_drift": "fallback_to_raw",
            "timestamps_immutable": True,
        },
    }
    report_path = base.JOB / "content-qa.json"
    atomic_json(report_path, report)
    _add_report_to_export_manifest(base.JOB, report_path)
    print(
        f"CONTENT_QA={report['status']} errors={len(errors)} "
        f"fallbacks={len(fallbacks)}"
    )
    return structural if structural else (0 if not errors else 2)


if __name__ == "__main__":
    raise SystemExit(main())
