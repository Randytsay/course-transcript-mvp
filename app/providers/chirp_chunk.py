"""Run and verify exactly one Chirp 3 chunk using private GCS output."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech

from app.live_features import words_to_text

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = DATA_DIR / "jobs" / JOB_NAME


def _env_true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DYNAMIC_BATCHING = _env_true("CHIRP_DYNAMIC_BATCHING", default=False)


def iso() -> str:
    return datetime.now(UTC).isoformat()


def elapsed_ms(start: str, end: str) -> int:
    return max(
        0,
        round(
            (
                datetime.fromisoformat(end)
                - datetime.fromisoformat(start)
            ).total_seconds()
            * 1000
        ),
    )


def atomic(path: Path, data: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def ms(value: object) -> int:
    return round(value.total_seconds() * 1000)  # type: ignore[attr-defined]


def has_speech(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
    if not match:
        raise RuntimeError("unable to determine chunk audio volume")
    return float(match.group(1)) > float(
        os.getenv("CHIRP_SPEECH_MEAN_VOLUME_DB", "-50")
    )


def _prior_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    index = int(os.environ["CHUNK_INDEX"])
    start = float(os.environ["CHUNK_START_SECONDS"])
    end = float(os.environ["CHUNK_END_SECONDS"])
    name = f"chunk-{index:03d}"
    chunk = JOB / "chunks" / name
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket_name = os.environ["GCS_BUCKET"]
    chunk.mkdir(parents=True, exist_ok=True)
    manifest_path = chunk / "manifest.json"
    prior = _prior_manifest(manifest_path)
    attempt_count = int(prior.get("attempt_count") or 0) + 1
    chunk_started_at = iso()

    (chunk / "partial-transcript.json").unlink(missing_ok=True)
    audio = chunk / "audio.flac"
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
    audio_ready_at = iso()

    bucket = storage.Client().bucket(bucket_name)
    object_name = f"jobs/{JOB_NAME}/chunks/{name}/audio.flac"
    blob = bucket.blob(object_name)
    blob.upload_from_filename(audio, content_type="audio/flac")
    upload_completed_at = iso()

    client = speech_v2.SpeechClient(
        client_options={"api_endpoint": "us-speech.googleapis.com"}
    )
    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=[os.getenv("LANGUAGE_CODE", "cmn-Hant-TW")],
        model="chirp_3",
        features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True),
    )
    uri = f"gs://{bucket_name}/{object_name}"
    output_uri = f"gs://{bucket_name}/jobs/{JOB_NAME}/chunks/{name}/chirp-output/"
    request = cloud_speech.BatchRecognizeRequest(
        recognizer=f"projects/{project}/locations/us/recognizers/_",
        config=config,
        files=[cloud_speech.BatchRecognizeFileMetadata(uri=uri)],
        recognition_output_config=cloud_speech.RecognitionOutputConfig(
            gcs_output_config=cloud_speech.GcsOutputConfig(uri=output_uri)
        ),
        processing_strategy=(
            cloud_speech.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING
            if DYNAMIC_BATCHING
            else cloud_speech.BatchRecognizeRequest.ProcessingStrategy.PROCESSING_STRATEGY_UNSPECIFIED
        ),
    )
    request_started_at = iso()
    operation = client.batch_recognize(request=request)
    submitted_at = iso()
    role = os.getenv("CHUNK_ROLE", "base")
    record: dict[str, object] = {
        "chunk_index": index,
        "role": role,
        "source_start_ms": round(start * 1000),
        "source_end_ms": round(end * 1000),
        "operation_name": operation.operation.name,
        "status": "SUBMITTED",
        "attempt_count": attempt_count,
        "chunk_started_at": chunk_started_at,
        "audio_ready_at": audio_ready_at,
        "upload_completed_at": upload_completed_at,
        "request_started_at": request_started_at,
        "submitted_at": submitted_at,
        "submit_latency_ms": elapsed_ms(request_started_at, submitted_at),
        "created_at": submitted_at,
        "processing_strategy": (
            "DYNAMIC_BATCHING" if DYNAMIC_BATCHING else "PROCESSING_STRATEGY_UNSPECIFIED"
        ),
        "dynamic_batching": DYNAMIC_BATCHING,
    }
    atomic(manifest_path, record)

    if os.getenv("SUBMIT_ONLY") == "1":
        print(
            f"CHIRP_{name}=SUBMITTED operation={operation.operation.name} "
            f"strategy={record['processing_strategy']}"
        )
        return

    response = operation.result(timeout=90_000 if DYNAMIC_BATCHING else 3_600)
    provider_completed_at = iso()
    file_result = response.results[uri]
    result_kind = file_result._pb.WhichOneof("result")
    error = (
        {"code": file_result.error.code, "message": file_result.error.message}
        if file_result.error and file_result.error.code
        else None
    )
    record.update(
        {
            "status": "FAILED",
            "error": error,
            "result_oneof": result_kind,
            "google_cloud_speech_version": importlib.metadata.version(
                "google-cloud-speech"
            ),
            "output_field": None,
            "gcs_uri": None,
            "provider_completed_at": provider_completed_at,
            "provider_processing_ms": elapsed_ms(
                submitted_at, provider_completed_at
            ),
        }
    )
    if (error and error["code"] != 0) or result_kind != "cloud_storage_result":
        atomic(manifest_path, record)
        raise RuntimeError(str(record))

    cloud_result = file_result.cloud_storage_result
    field = (
        "native_format_uri"
        if getattr(cloud_result, "native_format_uri", "")
        else "uri"
    )
    result_uri = getattr(cloud_result, field, "")
    if not result_uri:
        candidates = list(
            bucket.list_blobs(
                prefix=f"jobs/{JOB_NAME}/chunks/{name}/chirp-output/"
            )
        )
        if len(candidates) != 1:
            raise RuntimeError(
                f"Expected one GCS output object, found {len(candidates)}"
            )
        result_uri = f"gs://{bucket_name}/{candidates[0].name}"
        field = "gcs_prefix_fallback"
    record.update(output_field=field, gcs_uri=result_uri)

    result_bucket, result_name = result_uri.removeprefix("gs://").split("/", 1)
    raw = storage.Client().bucket(result_bucket).blob(result_name).download_as_text()
    raw_temp = chunk / "chirp-raw.json.tmp"
    raw_temp.write_text(raw, encoding="utf-8")
    raw_temp.replace(chunk / "chirp-raw.json")
    parsed = cloud_speech.BatchRecognizeResults.from_json(raw)
    words: list[dict[str, object]] = []
    for result in parsed.results:
        if not result.alternatives:
            continue
        for word in result.alternatives[0].words:
            words.append(
                {
                    "word": word.word,
                    "start_ms": ms(word.start_offset) + round(start * 1000),
                    "end_ms": ms(word.end_offset) + round(start * 1000),
                }
            )

    status = "SUCCEEDED"
    if not words:
        if has_speech(audio):
            record.update(
                status="FAILED",
                error={
                    "code": "EMPTY_WITH_SPEECH",
                    "message": "Chirp returned no words for audible speech",
                },
                word_count=0,
                max_end_ms=0,
            )
            atomic(manifest_path, record)
            raise RuntimeError("Chirp returned no words for audible chunk")
        status = "EMPTY_SILENCE"

    recovered_at = iso()
    raw_text = words_to_text(words)
    record.update(
        status=status,
        word_count=len(words),
        max_end_ms=max((int(word["end_ms"]) for word in words), default=0),
        recovered_at=recovered_at,
        recovery_delay_ms=elapsed_ms(provider_completed_at, recovered_at),
        total_wall_ms=elapsed_ms(submitted_at, recovered_at),
    )
    atomic(
        chunk / "partial-transcript.json",
        {
            "chunkIndex": index,
            "sourceStartMs": round(start * 1000),
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
    atomic(manifest_path, record)
    audio.unlink(missing_ok=True)
    print(f"CHIRP_{name}=PASS status={status} words={len(words)}")


if __name__ == "__main__":
    main()
