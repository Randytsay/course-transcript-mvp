"""Create a word-level Chirp 3 timing source for the approved sample."""
from __future__ import annotations

import json
import os
import subprocess
import traceback
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech

ROOT = Path("/app")
SOURCE = ROOT / "data" / "input" / "語音 260724_162531.m4a"
TMP = ROOT / "tmp"
RESULTS = ROOT / "data" / "results"


def offset_ms(value: object) -> int:
    return round(value.total_seconds() * 1000)


def main() -> int:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket_name = os.environ["GCS_BUCKET"]
    location = os.getenv("SPEECH_LOCATION", "us")
    seconds = int(os.getenv("PHASE2_SAMPLE_SECONDS", "300"))
    TMP.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    normalized = TMP / "phase3-sample.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(SOURCE), "-t", str(seconds), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(normalized)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    object_name = "test/phase3/sample-300s.flac"
    blob = storage.Client().bucket(bucket_name).blob(object_name)
    try:
        blob.upload_from_filename(normalized, content_type="audio/flac")
        client = speech_v2.SpeechClient(client_options={"api_endpoint": f"{location}-speech.googleapis.com"})
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=["cmn-Hans-CN"],
            model="chirp_3",
            features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True),
        )
        uri = f"gs://{bucket_name}/{object_name}"
        output_prefix = f"test/phase3/output/{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}/"
        operation = client.batch_recognize(
            request=cloud_speech.BatchRecognizeRequest(
                recognizer=f"projects/{project}/locations/{location}/recognizers/_",
                config=config,
                files=[cloud_speech.BatchRecognizeFileMetadata(uri=uri)],
                recognition_output_config=cloud_speech.RecognitionOutputConfig(
                    gcs_output_config=cloud_speech.GcsOutputConfig(
                        uri=f"gs://{bucket_name}/{output_prefix}"
                    )
                ),
            )
        )
        response = operation.result(timeout=600)
        result_uri = response.results[uri].uri
        output_bucket, output_name = result_uri.removeprefix("gs://").split("/", 1)
        raw_result = storage.Client().bucket(output_bucket).blob(output_name).download_as_text()
        transcript = cloud_speech.BatchRecognizeResults.from_json(raw_result).results
        words = []
        result_summaries = []
        for result in transcript:
            alternative = result.alternatives[0]
            result_summaries.append(
                {
                    "alternative_count": len(result.alternatives),
                    "transcript_characters": len(alternative.transcript),
                    "word_count": len(alternative.words),
                }
            )
            for word in alternative.words:
                words.append({
                    "word": word.word,
                    "start_ms": offset_ms(word.start_offset),
                    "end_ms": offset_ms(word.end_offset),
                    "speaker_label": word.speaker_label,
                })
        output = {
            "created_at": datetime.now(UTC).isoformat(),
            "model": "chirp_3",
            "location": location,
            "language_code": "cmn-Hans-CN",
            "sample_seconds": seconds,
            "word_count": len(words),
            "max_end_ms": max((word["end_ms"] for word in words), default=0),
            "words": words,
        }
        (ROOT / "logs" / "phase3-chirp3-response-summary.json").write_text(
            json.dumps(
                {
                    "batch_result_entries": len(response.results),
                    "transcript_result_count": len(transcript),
                    "result_summaries": result_summaries,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (RESULTS / "phase3-chirp3-words.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"PHASE3_CHIRP=PASS words={len(words)}")
    except Exception as error:
        (ROOT / "logs" / "phase3-chirp3-diagnostic.json").write_text(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    finally:
        normalized.unlink(missing_ok=True)
        for object_blob in storage.Client().list_blobs(bucket_name, prefix="test/phase3/"):
            object_blob.delete()


if __name__ == "__main__":
    main()
