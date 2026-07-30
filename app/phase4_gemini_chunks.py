"""Transcribe the approved sample in overlapping Gemini 3.6 Flash chunks.

This avoids treating one long model response as complete when the model ends its
output before the media does.  It deliberately uses only the user-approved
Gemini 3.6 Flash model; Chirp remains a separate timing reference.
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

# The 10-second overlap makes the join auditable without reusing the prior
# incomplete five-minute output as an authority.
CHUNKS = ((0, 180), (170, 130))


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
                        "start_ms": {"type": "integer"},
                        "end_ms": {"type": "integer"},
                        "speaker": {"type": "string"},
                        "text_verbatim": {"type": "string"},
                        "unclear": {"type": "boolean"},
                    },
                    "required": ["start_ms", "end_ms", "speaker", "text_verbatim", "unclear"],
                },
            },
        },
        "required": ["language", "segments"],
    }


def transcribe_chunk(client: genai.Client, bucket: storage.Bucket, model: str, start_s: int, duration_s: int) -> dict[str, object]:
    audio_path = TMP / f"phase4-{start_s:04d}.flac"
    object_name = f"test/phase4/chunk-{start_s:04d}.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start_s), "-i", str(SOURCE), "-t", str(duration_s), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(audio_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    blob = bucket.blob(object_name)
    try:
        blob.upload_from_filename(audio_path, content_type="audio/flac")
        response = client.models.generate_content(
            model=model,
            contents=[
                "Transcribe this Traditional-Chinese recording faithfully. Return JSON only. "
                "Use local audio milliseconds, Speaker A/B labels only, preserve spoken wording, "
                "and use [聽不清] with unclear=true instead of guessing. Do not summarize or omit speech.",
                types.Part.from_uri(file_uri=f"gs://{bucket.name}/{object_name}", mime_type="audio/flac"),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema(),
                audio_timestamp=True,
                temperature=0,
                max_output_tokens=8192,
            ),
        )
        payload = json.loads(response.text)
        segments = []
        for segment in payload["segments"]:
            segments.append({
                **segment,
                "start_ms": int(segment["start_ms"]) + start_s * 1000,
                "end_ms": int(segment["end_ms"]) + start_s * 1000,
            })
        usage = response.usage_metadata
        return {
            "start_ms": start_s * 1000,
            "end_ms": (start_s + duration_s) * 1000,
            "segment_count": len(segments),
            "segments": segments,
            "usage_metadata": usage.model_dump(mode="json") if usage else None,
        }
    finally:
        audio_path.unlink(missing_ok=True)


def main() -> int:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Expected approved sample at {SOURCE}")
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket_name = os.environ["GCS_BUCKET"]
    model = os.environ["PHASE2_MODEL"]
    if model != "gemini-3.6-flash":
        raise RuntimeError("Phase 4 is locked to the approved gemini-3.6-flash model.")
    TMP.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    bucket = storage.Client().bucket(bucket_name)
    client = genai.Client(vertexai=True, project=project, location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    started = datetime.now(UTC)
    try:
        chunks = [transcribe_chunk(client, bucket, model, start_s, duration_s) for start_s, duration_s in CHUNKS]
        # Use the first chunk up to the overlap boundary and the second from it.
        boundary_ms = CHUNKS[1][0] * 1000
        merged = [segment for segment in chunks[0]["segments"] if segment["end_ms"] <= boundary_ms]
        merged.extend(segment for segment in chunks[1]["segments"] if segment["start_ms"] >= boundary_ms)
        merged.sort(key=lambda segment: (segment["start_ms"], segment["end_ms"]))
        output = {
            "created_at": datetime.now(UTC).isoformat(),
            "model": model,
            "location": os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            "sample_seconds": 300,
            "chunk_plan": [{"start_s": start, "duration_s": duration} for start, duration in CHUNKS],
            "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
            "chunks": chunks,
            "segments": merged,
            "max_end_ms": max((segment["end_ms"] for segment in merged), default=0),
        }
        (RESULTS / "phase4-gemini-3.6-flash-chunks.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (RESULTS / "phase4-gemini-3.6-flash-chunks.txt").write_text(
            "\n".join(
                f"[{segment['start_ms'] / 1000:07.2f}] {segment['speaker']}: {segment['text_verbatim']}"
                for segment in merged
            ) + "\n",
            encoding="utf-8",
        )
        print(f"PHASE4_GEMINI=PASS segments={len(merged)} max_end_ms={output['max_end_ms']}")
        return 0
    finally:
        for blob in storage.Client().list_blobs(bucket_name, prefix="test/phase4/"):
            blob.delete()


if __name__ == "__main__":
    raise SystemExit(main())
