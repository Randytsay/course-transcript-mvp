"""Run the complete worker with isolated fake providers and no cloud requests."""
from __future__ import annotations

import os
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

from app.jobs.store import JobStore
from app.pipeline import worker

REQUIRED = (
    "transcript_raw.txt",
    "transcript_corrected.txt",
    "transcript_timestamped.txt",
    "transcript.srt",
    "transcript.vtt",
    "transcript.json",
    "transcript.csv",
    "transcript.docx",
    "transcript.pdf",
    "glossary_candidates.csv",
    "glossary_decisions.yaml",
    "join_qa.json",
    "qa_report.json",
    "qa_report.html",
    "usage_report.json",
    "processing_manifest.json",
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="course-transcript-fake-e2e-") as root:
        data_dir = Path(root)
        store = JobStore(data_dir / "course-transcript.db")
        preview = store.create_preview(
            source_path="gdrive:test/fake-e2e.wav",
            source_name="fake-e2e.wav",
            size_bytes=100,
            modified_at=None,
            mime_type="audio/wav",
            actor="fake-e2e",
        )
        job = store.create_preflight_job(
            preview_id=preview["id"],
            language_code="cmn-Hant-TW",
            profile="highest_accuracy",
            enable_gemini_correction=True,
            enable_subtitles=True,
            require_human_review=True,
            actor="fake-e2e",
        )
        store.acquire_lease(job["id"], "fake-preflight")
        estimated = store.record_preflight_result(
            job_id=job["id"],
            duration_seconds=6,
            source_checksum="0" * 64,
            media_format="wav",
            audio_codec="pcm_s16le",
            estimated_cost_usd=Decimal("0.01"),
            pricing_version="fake-no-charge",
            worker_id="fake-preflight",
        )
        store.approve_job(
            job_id=job["id"],
            expected_revision=estimated["revision"],
            confirmed_estimated_cost_usd=Decimal("0.01"),
            project_limit_usd=Decimal("200"),
            actor="fake-e2e",
        )
        job_dir = data_dir / "jobs" / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=6",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "flac",
                str(job_dir / "normalized.flac"),
            ],
            check=True,
        )
        previous_data_dir = os.environ.get("COURSE_TRANSCRIPT_DATA_DIR")
        previous_fake = os.environ.get("COURSE_TRANSCRIPT_FAKE_PROVIDER")
        os.environ["COURSE_TRANSCRIPT_DATA_DIR"] = str(data_dir)
        os.environ["COURSE_TRANSCRIPT_FAKE_PROVIDER"] = "1"
        try:
            worker.run_once(
                store,
                data_dir=data_dir,
                worker_id="fake-pipeline-worker",
            )
        finally:
            if previous_data_dir is None:
                os.environ.pop("COURSE_TRANSCRIPT_DATA_DIR", None)
            else:
                os.environ["COURSE_TRANSCRIPT_DATA_DIR"] = previous_data_dir
            if previous_fake is None:
                os.environ.pop("COURSE_TRANSCRIPT_FAKE_PROVIDER", None)
            else:
                os.environ["COURSE_TRANSCRIPT_FAKE_PROVIDER"] = previous_fake
        finished = store.get_job(job["id"])
        missing = [
            name
            for name in REQUIRED
            if not (job_dir / name).is_file()
            or (job_dir / name).stat().st_size <= 0
        ]
        if finished["status"] != "awaiting_review" or missing:
            print(
                f"FAKE_WORKER_E2E=FAIL status={finished['status']} "
                f"missing={len(missing)}"
            )
            return 2
        print(
            f"FAKE_WORKER_E2E=PASS status={finished['status']} "
            f"artifacts={len(REQUIRED)}/{len(REQUIRED)} paid_requests=0"
        )
    print("FAKE_WORKER_E2E_CLEANUP=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
