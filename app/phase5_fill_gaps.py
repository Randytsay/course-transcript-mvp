"""Fill only the verified coverage gaps in the Gemini 3.6 Flash sample."""
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
PREVIOUS = RESULTS / "phase4-gemini-3.6-flash-chunks.json"
# Chosen from actual Phase 4 coverage, not speculative retries.
GAP_CHUNKS = ((120, 65), (235, 65))


def response_schema() -> dict[str, object]:
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


def run_chunk(client: genai.Client, bucket: storage.Bucket, model: str, start_s: int, duration_s: int) -> dict[str, object]:
    audio = TMP / f"phase5-{start_s:04d}.flac"
    object_name = f"test/phase5/chunk-{start_s:04d}.flac"
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
                response_mime_type="application/json", response_json_schema=response_schema(),
                audio_timestamp=True, temperature=0, max_output_tokens=8192,
            ),
        )
        transcript = json.loads(response.text)
        absolute_segments = [{
            **segment,
            "start_ms": int(segment["start_ms"]) + start_s * 1000,
            "end_ms": int(segment["end_ms"]) + start_s * 1000,
        } for segment in transcript["segments"]]
        usage = response.usage_metadata
        return {
            "start_ms": start_s * 1000, "end_ms": (start_s + duration_s) * 1000,
            "segment_count": len(absolute_segments), "segments": absolute_segments,
            "usage_metadata": usage.model_dump(mode="json") if usage else None,
        }
    finally:
        audio.unlink(missing_ok=True)


def main() -> int:
    previous = json.loads(PREVIOUS.read_text(encoding="utf-8"))
    model = os.environ["PHASE2_MODEL"]
    if model != "gemini-3.6-flash":
        raise RuntimeError("Phase 5 is locked to the approved gemini-3.6-flash model.")
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket_name = os.environ["GCS_BUCKET"]
    TMP.mkdir(parents=True, exist_ok=True)
    bucket = storage.Client().bucket(bucket_name)
    client = genai.Client(vertexai=True, project=project, location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    started = datetime.now(UTC)
    try:
        fills = [run_chunk(client, bucket, model, start, duration) for start, duration in GAP_CHUNKS]
        first = previous["chunks"][0]["segments"]
        second = previous["chunks"][1]["segments"]
        merged = [segment for segment in first if segment["end_ms"] <= 120_000]
        merged.extend(segment for segment in fills[0]["segments"] if segment["start_ms"] >= 120_000)
        merged.extend(segment for segment in second if segment["start_ms"] >= 185_000 and segment["end_ms"] <= 235_000)
        merged.extend(segment for segment in fills[1]["segments"] if segment["start_ms"] >= 235_000)
        merged.sort(key=lambda segment: (segment["start_ms"], segment["end_ms"]))
        payload = {
            "created_at": datetime.now(UTC).isoformat(), "model": model,
            "location": os.getenv("GOOGLE_CLOUD_LOCATION", "global"), "sample_seconds": 300,
            "source_phase4": PREVIOUS.name, "gap_chunk_plan": [{"start_s": a, "duration_s": b} for a, b in GAP_CHUNKS],
            "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(), "gap_chunks": fills,
            "segments": merged, "max_end_ms": max((s["end_ms"] for s in merged), default=0),
        }
        (RESULTS / "phase5-gemini-3.6-flash-complete.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (RESULTS / "phase5-gemini-3.6-flash-complete.txt").write_text(
            "\n".join(f"[{s['start_ms'] / 1000:07.2f}] {s['speaker']}: {s['text_verbatim']}" for s in merged) + "\n",
            encoding="utf-8",
        )
        print(f"PHASE5_GEMINI=PASS segments={len(merged)} max_end_ms={payload['max_end_ms']}")
        return 0
    finally:
        for blob in storage.Client().list_blobs(bucket_name, prefix="test/phase5/"):
            blob.delete()


if __name__ == "__main__":
    raise SystemExit(main())
