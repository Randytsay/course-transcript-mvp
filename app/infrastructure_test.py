"""Minimal, destructive-safe infrastructure checks for Course Transcript MVP."""
from __future__ import annotations
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from google import genai
from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech
from google.genai import types

ROOT = Path("/app")
LOG_DIR = ROOT / "logs"
TMP_DIR = ROOT / "tmp"
REPORT_PATH = LOG_DIR / "infrastructure-test-report.md"
DETAIL_PATH = LOG_DIR / "infrastructure-test-results.json"

@dataclass
class Check:
    name: str
    status: str
    detail: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

def safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error)[:600]}"

def gcs_check(bucket_name: str, prefix: str) -> Check:
    client = storage.Client()
    object_name = f"{prefix}/connection-test.txt"
    expected = b"Hello from Oracle VPS"
    blob = client.bucket(bucket_name).blob(object_name)
    created = False
    try:
        blob.upload_from_string(expected, content_type="text/plain; charset=utf-8")
        created = True
        if blob.download_as_bytes() != expected:
            raise RuntimeError("Downloaded GCS content did not match uploaded content")
        blob.reload()
        metadata = {"object": object_name, "size": blob.size, "content_type": blob.content_type, "generation": str(blob.generation)}
        blob.delete()
        if blob.exists(client=client):
            raise RuntimeError("GCS test object still exists after deletion")
        return Check("GCS round-trip", "PASS", "Upload, download, metadata, and deletion verified.", metadata=metadata)
    except Exception as error:
        return Check("GCS round-trip", "FAIL", "GCS round-trip did not complete.", safe_error(error), {"object": object_name})
    finally:
        if created:
            try:
                if blob.exists(client=client):
                    blob.delete()
            except Exception:
                pass

def vertex_check(project: str, location: str, model: str) -> Check:
    try:
        client = genai.Client(vertexai=True, project=project, location=location)
        response = client.models.generate_content(
            model=model,
            contents="Respond with exactly: VERTEX_OK",
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=64),
        )
        actual = (response.text or "").strip()
        if actual != "VERTEX_OK":
            raise RuntimeError(f"Unexpected response: {actual!r}")
        return Check("Vertex AI / Gemini", "PASS", "Minimal Gemini response verified.", metadata={"sdk": "google-genai", "model": model, "location": location})
    except Exception as error:
        return Check("Vertex AI / Gemini", "FAIL", "Minimal Gemini request did not complete.", safe_error(error), {"sdk": "google-genai", "model": model, "location": location})

def speech_check(project: str, location: str) -> Check:
    wav_path = TMP_DIR / "test-silence-2s.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "2", "-c:a", "pcm_s16le", str(wav_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        client = speech_v2.SpeechClient(
            client_options={"api_endpoint": f"{location}-speech.googleapis.com"}
        )
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=["en-US"],
            model="chirp_3",
        )
        response = client.recognize(
            request=cloud_speech.RecognizeRequest(
                recognizer=f"projects/{project}/locations/{location}/recognizers/_",
                config=config,
                content=wav_path.read_bytes(),
            ),
            timeout=60,
        )
        return Check("Speech-to-Text", "PASS", "2-second silent WAV request completed; an empty transcript is expected.", metadata={"sdk": "google-cloud-speech", "model": "chirp_3", "location": location, "result_count": len(response.results)})
    except Exception as error:
        return Check("Speech-to-Text", "FAIL", "Minimal silent-audio request did not complete.", safe_error(error), {"sdk": "google-cloud-speech", "model": "chirp_3", "location": location})
    finally:
        wav_path.unlink(missing_ok=True)

def render_report(checks: list[Check], context: dict[str, str]) -> str:
    lines = [
        "# Course Transcript MVP — Infrastructure Test Report", "",
        f"- Executed (UTC): {datetime.now(UTC).isoformat()}",
        f"- OS / architecture: {platform.platform()} / {platform.machine()}",
        f"- Python: {sys.version.split()[0]}",
        f"- Docker image test runtime: {context['runtime']}",
        f"- Gemini model / location: {context['vertex_model']} / {context['vertex_location']}",
        f"- Speech model / location: chirp_3 / {context['speech_location']}", "",
        "## Results", "", "| Test | Status | Detail |", "|---|---|---|",
    ]
    lines.extend(f"| {item.name} | {item.status} | {item.detail} |" for item in checks)
    lines.extend(["", "## Errors and remediation", ""])
    for item in checks:
        if item.error:
            lines.extend([f"### {item.name}", "", f"- Error: {item.error}", "- Remediation: review the service/API/IAM configuration named by the error; no automatic IAM or resource changes were made.", ""])
    if all(item.status == "PASS" for item in checks):
        lines.extend(["No failures occurred.", "", "## Next step", "", "Proceed only after user approval to Phase 2: MVP project scaffolding and a real 5-minute media test."])
    return "\n".join(lines) + "\n"

def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    project = require_env("GOOGLE_CLOUD_PROJECT")
    bucket = require_env("GCS_BUCKET")
    prefix = os.getenv("INFRA_TEST_PREFIX", "test").strip("/") or "test"
    vertex_location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    vertex_model = os.getenv("VERTEX_MODEL", "gemini-3.7-flash")
    speech_location = os.getenv("SPEECH_LOCATION", "global")
    checks = [gcs_check(bucket, prefix), vertex_check(project, vertex_location, vertex_model), speech_check(project, speech_location)]
    context = {"runtime": "Docker", "vertex_model": vertex_model, "vertex_location": vertex_location, "speech_location": speech_location}
    REPORT_PATH.write_text(render_report(checks, context), encoding="utf-8")
    DETAIL_PATH.write_text(json.dumps({"context": context, "checks": [asdict(item) for item in checks]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in checks:
        print(f"{item.name}: {item.status}")
        if item.error:
            print(f"  {item.error}")
    return 0 if all(item.status == "PASS" for item in checks) else 1

if __name__ == "__main__":
    raise SystemExit(main())
