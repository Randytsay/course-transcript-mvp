"""Recover a completed chunk from its GCS result without a new ASR call."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import storage
from google.cloud.speech_v2.types import cloud_speech

from app.live_features import words_to_text
from app.providers.chirp_chunk import has_speech

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = DATA_DIR / "jobs" / JOB_NAME


def iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def elapsed_ms(start: object, end: object) -> int:
    if not start or not end:
        return 0
    try:
        first = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        second = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, round((second - first).total_seconds() * 1000))


def ms(value: object) -> int:
    return round(value.total_seconds() * 1000)  # type: ignore[attr-defined]


def atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    index = int(os.environ.get("CHUNK_INDEX", "0"))
    start = float(os.environ.get("CHUNK_START_SECONDS", "0"))
    end = float(os.environ.get("CHUNK_END_SECONDS", "900"))
    name = f"chunk-{index:03d}"
    chunk = JOB / "chunks" / name
    prior_path = chunk / "manifest.json"
    try:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        prior = {}

    bucket = storage.Client().bucket(os.environ["GCS_BUCKET"])
    blobs = list(
        bucket.list_blobs(prefix=f"jobs/{JOB_NAME}/chunks/{name}/chirp-output/")
    )
    if not blobs and os.environ.get("ALLOW_PENDING") == "1":
        prior["last_recovery_check_at"] = iso()
        prior["status"] = "RECOVERING"
        atomic(prior_path, prior)
        print(f"RECOVER_{name}=PENDING")
        raise SystemExit(75)
    if len(blobs) != 1:
        raise RuntimeError(f"Expected one result object, found {len(blobs)}")

    blob = blobs[0]
    provider_completed_at = iso(blob.updated) if blob.updated else iso()
    recovery_started_at = iso()
    raw = blob.download_as_text()
    raw_temporary = chunk / "chirp-raw.json.tmp"
    raw_temporary.write_text(raw, encoding="utf-8")
    raw_temporary.replace(chunk / "chirp-raw.json")
    parsed = cloud_speech.BatchRecognizeResults.from_json(raw)
    offset = round(start * 1000)
    words: list[dict[str, object]] = []
    for result in parsed.results:
        if not result.alternatives:
            continue
        for word in result.alternatives[0].words:
            words.append(
                {
                    "word": word.word,
                    "start_ms": ms(word.start_offset) + offset,
                    "end_ms": ms(word.end_offset) + offset,
                }
            )

    audio = chunk / "audio.flac"
    if not words:
        if not audio.exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(start),
                    "-i",
                    str(JOB / "normalized.flac"),
                    "-t",
                    str(end - start),
                    "-c:a",
                    "flac",
                    str(audio),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        if has_speech(audio):
            raise RuntimeError(
                "Recovered Chirp result has no words for audible chunk"
            )
    status = "SUCCEEDED" if words else "EMPTY_SILENCE"
    recovered_at = iso()
    submitted_at = prior.get("submitted_at") or prior.get("created_at")
    payload = {
        **prior,
        "chunk_index": index,
        "role": os.environ.get("CHUNK_ROLE", prior.get("role", "base")),
        "source_start_ms": offset,
        "source_end_ms": round(end * 1000),
        "operation_name": prior.get("operation_name"),
        "status": status,
        "result_oneof": "cloud_storage_result",
        "output_field": "gcs_prefix_recovery",
        "gcs_uri": f"gs://{bucket.name}/{blob.name}",
        "word_count": len(words),
        "max_end_ms": max((int(word["end_ms"]) for word in words), default=0),
        "provider_completed_at": provider_completed_at,
        "recovery_started_at": recovery_started_at,
        "recovered_at": recovered_at,
        "provider_processing_ms": elapsed_ms(submitted_at, provider_completed_at),
        "recovery_delay_ms": elapsed_ms(provider_completed_at, recovery_started_at),
        "recovery_download_ms": elapsed_ms(recovery_started_at, recovered_at),
        "total_wall_ms": elapsed_ms(submitted_at, recovered_at),
        "attempt_count": int(prior.get("attempt_count") or 1),
    }
    raw_text = words_to_text(words)
    atomic(
        chunk / "partial-transcript.json",
        {
            "chunkIndex": index,
            "sourceStartMs": offset,
            "sourceEndMs": round(end * 1000),
            "status": status,
            "wordCount": len(words),
            "rawText": raw_text,
            "firstWordMs": words[0]["start_ms"] if words else None,
            "lastWordMs": words[-1]["end_ms"] if words else None,
            "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "completedAt": recovered_at,
        },
    )
    atomic(chunk / "words.json", {"chunk_index": index, "words": words})
    atomic(chunk / "manifest.json", payload)
    audio.unlink(missing_ok=True)
    print(f"RECOVER_{name}=PASS status={status} words={len(words)}")


if __name__ == "__main__":
    main()
