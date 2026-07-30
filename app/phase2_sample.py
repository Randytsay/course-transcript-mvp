"""Run the approved 5-minute real-media Gemini transcription sample."""
from __future__ import annotations

import hashlib
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket_name = os.environ["GCS_BUCKET"]
    seconds = int(os.getenv("PHASE2_SAMPLE_SECONDS", "300"))
    model = os.getenv("PHASE2_MODEL", "gemini-2.5-flash")
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Expected approved sample at {SOURCE}")
    TMP.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    normalized = TMP / "phase2-sample.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(SOURCE), "-t", str(seconds), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(normalized)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    fingerprint = sha256(normalized)
    object_name = f"test/phase2/{fingerprint}/audio.flac"
    blob = storage.Client().bucket(bucket_name).blob(object_name)
    started = datetime.now(UTC)
    try:
        blob.upload_from_filename(normalized, content_type="audio/flac")
        client = genai.Client(vertexai=True, project=project, location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
        schema = {
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
        prompt = (
            "Transcribe this Traditional-Chinese recording faithfully. Return JSON only. "
            "Use local audio milliseconds, Speaker A/B labels only, preserve spoken wording, "
            "and use [聽不清] with unclear=true instead of guessing."
        )
        response = client.models.generate_content(
            model=model,
            contents=[prompt, types.Part.from_uri(file_uri=f"gs://{bucket_name}/{object_name}", mime_type="audio/flac")],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema,
                audio_timestamp=True,
                temperature=0,
            ),
        )
        transcript = json.loads(response.text)
        usage = response.usage_metadata
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "source_file": SOURCE.name,
            "source_unchanged_sha256": sha256(SOURCE),
            "sample_seconds": seconds,
            "normalized_sha256": fingerprint,
            "model": model,
            "location": os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
            "usage_metadata": usage.model_dump(mode="json") if usage else None,
            "transcript": transcript,
        }
        safe_model = "".join(character if character.isalnum() or character in ".-" else "_" for character in model)
        output_json = RESULTS / f"phase2-{safe_model}-transcript.json"
        output_txt = RESULTS / f"phase2-{safe_model}-transcript.txt"
        output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_txt.write_text(
            "\n".join(
                f"[{segment['start_ms'] / 1000:07.2f}] {segment['speaker']}: {segment['text_verbatim']}"
                for segment in transcript["segments"]
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"PHASE2_TRANSCRIPT=PASS segments={len(transcript['segments'])}")
        return 0
    finally:
        normalized.unlink(missing_ok=True)
        if blob.exists():
            blob.delete()


if __name__ == "__main__":
    raise SystemExit(main())
