"""Submit one durable Chirp 3 operation with an attempt-isolated GCS prefix."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech
from app.providers.mantra_context import speech_adaptation, speech_adaptation_enabled

from app.providers.hardening_common import atomic_json, env_true, iso, window_matches

DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "voice_11386603-seg1")
JOB = DATA_DIR / "jobs" / JOB_NAME
DYNAMIC_BATCHING = env_true("CHIRP_DYNAMIC_BATCHING", default=False)


def _load(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    index = int(os.environ["CHUNK_INDEX"])
    start = float(os.environ["CHUNK_START_SECONDS"])
    end = float(os.environ["CHUNK_END_SECONDS"])
    if end <= start:
        raise RuntimeError("invalid Chirp chunk window")

    name = f"chunk-{index:03d}"
    chunk = JOB / "chunks" / name
    chunk.mkdir(parents=True, exist_ok=True)
    manifest_path = chunk / "manifest.json"
    prior = _load(manifest_path)
    prior_status = str(prior.get("status") or "")
    if prior_status in {
        "SUBMITTED",
        "RUNNING",
        "RECOVERING",
        "CANCEL_REQUESTED",
        "SUCCEEDED",
        "EMPTY_SILENCE",
    }:
        if not window_matches(
            prior,
            start_seconds=start,
            end_seconds=end,
            dynamic_batching=DYNAMIC_BATCHING,
        ):
            raise RuntimeError(
                "retained Chirp operation window or processing strategy is incompatible"
            )
        print(
            f"CHIRP_{name}=RETAINED status={prior_status} "
            f"operation={prior.get('operation_name')}"
        )
        return 0

    attempt_count = int(prior.get("attempt_count") or 0) + 1
    attempt_id = f"attempt-{attempt_count:03d}-{uuid.uuid4().hex[:8]}"
    audio = chunk / "audio.flac"
    (chunk / "partial-transcript.json").unlink(missing_ok=True)
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

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket_name = os.environ["GCS_BUCKET"]
    bucket = storage.Client().bucket(bucket_name)
    input_object = f"jobs/{JOB_NAME}/chunks/{name}/{attempt_id}/audio.flac"
    output_prefix = f"jobs/{JOB_NAME}/chunks/{name}/{attempt_id}/chirp-output/"
    bucket.blob(input_object).upload_from_filename(audio, content_type="audio/flac")

    client = speech_v2.SpeechClient(
        client_options={"api_endpoint": "us-speech.googleapis.com"}
    )
    config_kwargs = {
        "auto_decoding_config": cloud_speech.AutoDetectDecodingConfig(),
        "language_codes": [os.getenv("LANGUAGE_CODE", "cmn-Hant-TW")],
        "model": "chirp_3",
        "features": cloud_speech.RecognitionFeatures(enable_word_time_offsets=True),
    }
    if speech_adaptation_enabled():
        config_kwargs["adaptation"] = speech_adaptation()
    config = cloud_speech.RecognitionConfig(
        **config_kwargs,
    )
    input_uri = f"gs://{bucket_name}/{input_object}"
    output_uri = f"gs://{bucket_name}/{output_prefix}"
    request = cloud_speech.BatchRecognizeRequest(
        recognizer=f"projects/{project}/locations/us/recognizers/_",
        config=config,
        files=[cloud_speech.BatchRecognizeFileMetadata(uri=input_uri)],
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
    record = {
        "chunk_index": index,
        "role": os.getenv("CHUNK_ROLE", "base"),
        "patch_mode": os.getenv("CHUNK_PATCH_MODE", ""),
        "source_start_ms": round(start * 1000),
        "source_end_ms": round(end * 1000),
        "operation_name": operation.operation.name,
        "status": "SUBMITTED",
        "attempt_count": attempt_count,
        "attempt_id": attempt_id,
        "input_object_name": input_object,
        "input_uri": input_uri,
        "output_prefix": output_prefix,
        "output_uri": output_uri,
        "request_started_at": request_started_at,
        "submitted_at": submitted_at,
        "created_at": submitted_at,
        "processing_strategy": (
            "DYNAMIC_BATCHING"
            if DYNAMIC_BATCHING
            else "PROCESSING_STRATEGY_UNSPECIFIED"
        ),
        "dynamic_batching": DYNAMIC_BATCHING,
    }
    atomic_json(manifest_path, record)
    print(
        f"CHIRP_{name}=SUBMITTED operation={operation.operation.name} "
        f"strategy={record['processing_strategy']} attempt={attempt_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
