"""Obtain complete Gemini 3.6 Flash text in 30-second audio units.

Gemini's reported audio timestamps are retained as untrusted evidence only.
The known source windows are deterministic and the final SRT will be aligned to
the separate Chirp word timeline.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from google import genai
from google.cloud import storage
from google.genai import types

ROOT = Path("/app")
SOURCE = ROOT / "data" / "input" / "語音 260724_162531.m4a"
TMP = ROOT / "tmp"
RESULTS = ROOT / "data" / "results"
SAMPLE_SECONDS = 300
CHUNK_SECONDS = 30


def schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "language": {"type": "string"},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_ms": {"type": "integer"}, "end_ms": {"type": "integer"},
                        "speaker": {"type": "string"}, "text_verbatim": {"type": "string"},
                        "unclear": {"type": "boolean"},
                    },
                    "required": ["start_ms", "end_ms", "speaker", "text_verbatim", "unclear"],
                },
            },
        },
        "required": ["language", "segments"],
    }


def transcribe(client: genai.Client, bucket: storage.Bucket, model: str, start_s: int, duration_s: int) -> dict[str, object]:
    audio = TMP / f"phase6-{start_s:04d}.flac"
    object_name = f"test/phase6/chunk-{start_s:04d}.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start_s), "-i", str(SOURCE), "-t", str(duration_s), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(audio)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    blob = bucket.blob(object_name)
    try:
        blob.upload_from_filename(audio, content_type="audio/flac")
        response = client.models.generate_content(
            model=model,
            contents=[
                "Transcribe this Traditional-Chinese recording faithfully. Return JSON only. "
                "Use local audio milliseconds, Speaker A/B labels only, preserve spoken wording, "
                "and use [聽不清] with unclear=true instead of guessing. Do not summarize or omit speech.",
                types.Part.from_uri(file_uri=f"gs://{bucket.name}/{object_name}", mime_type="audio/flac"),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_json_schema=schema(),
                audio_timestamp=True, temperature=0, max_output_tokens=8192,
            ),
        )
        payload = json.loads(response.text)
        usage = response.usage_metadata
        return {
            "source_start_ms": start_s * 1000,
            "source_end_ms": (start_s + duration_s) * 1000,
            "segments": payload["segments"],
            "segment_count": len(payload["segments"]),
            "usage_metadata": usage.model_dump(mode="json") if usage else None,
        }
    finally:
        audio.unlink(missing_ok=True)


def main() -> int:
    model = os.environ["PHASE2_MODEL"]
    if model != "gemini-3.6-flash":
        raise RuntimeError("Phase 6 is locked to the approved gemini-3.6-flash model.")
    project, bucket_name = os.environ["GOOGLE_CLOUD_PROJECT"], os.environ["GCS_BUCKET"]
    TMP.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    client = genai.Client(vertexai=True, project=project, location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    bucket = storage.Client().bucket(bucket_name)
    started = datetime.now(UTC)
    specs = [(start, min(CHUNK_SECONDS, SAMPLE_SECONDS - start)) for start in range(0, SAMPLE_SECONDS, CHUNK_SECONDS)]
    try:
        chunks = [transcribe(client, bucket, model, start, duration) for start, duration in specs]
        combined_text = "\n".join(
            segment["text_verbatim"] for chunk in chunks for segment in chunk["segments"]
        )
        payload = {
            "created_at": datetime.now(UTC).isoformat(), "model": model,
            "location": os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            "sample_seconds": SAMPLE_SECONDS, "chunk_seconds": CHUNK_SECONDS,
            "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(), "chunks": chunks,
        }
        (RESULTS / "phase6-gemini-3.6-flash-microchunks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (RESULTS / "phase6-gemini-3.6-flash-microchunks.txt").write_text(combined_text + "\n", encoding="utf-8")
        print(f"PHASE6_GEMINI=PASS chunks={len(chunks)} chars={len(combined_text)}")
        return 0
    finally:
        for blob in storage.Client().list_blobs(bucket_name, prefix="test/phase6/"):
            blob.delete()


if __name__ == "__main__":
    raise SystemExit(main())
