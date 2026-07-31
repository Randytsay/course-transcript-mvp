"""Gemini 3.6 Flash text-only correction for immutable subtitle segments."""
from __future__ import annotations

import concurrent.futures
import csv
import json
import os
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path("/app")
JOB = ROOT / "data" / "jobs" / os.environ.get("JOB_NAME", "voice_11386603-seg1")
WORK = JOB / "correction-v2"
MODEL = "gemini-3.6-flash"
WINDOW_MS, MAX_WORKERS = 30_000, 3
_CLIENT: genai.Client | None = None

TERMS_SCHEMA = {"type": "object", "properties": {"terms": {"type": "array", "items": {"type": "object", "properties": {"canonical": {"type": "string"}, "variants": {"type": "array", "items": {"type": "string"}}, "confidence": {"type": "string"}}, "required": ["canonical", "variants", "confidence"]}}}, "required": ["terms"]}
CORRECTION_SCHEMA = {"type": "object", "properties": {"segments": {"type": "array", "items": {"type": "object", "properties": {"segment_id": {"type": "string"}, "corrected_text": {"type": "string"}, "uncertain_terms": {"type": "array", "items": {"type": "string"}}}, "required": ["segment_id", "corrected_text", "uncertain_terms"]}}}, "required": ["segments"]}


def atomic_text(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def client() -> genai.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"], location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    return _CLIENT


def generate_terms(raw_segments: list[dict]) -> list[dict]:
    output = JOB / "glossary"
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "global-terms.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")).get("terms", [])
    source = [{"segment_id": item["segment_id"], "text": item["raw_text"]} for item in raw_segments]
    prompt = """Extract only repeated or domain-specific terminology from this Traditional Chinese ASR transcript. Do not rewrite the transcript. For each term return a canonical spelling, observed variants, and confidence high/medium/low. Unknown terms must remain low confidence. JSON only.\n\n""" + json.dumps(source, ensure_ascii=False)
    response = client().models.generate_content(model=MODEL, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=TERMS_SCHEMA, temperature=0))
    payload = json.loads(response.text)
    record = {"model": MODEL, "usage_metadata": response.usage_metadata.model_dump(mode="json") if response.usage_metadata else None, "terms": payload.get("terms", []), "raw_response": response.text}
    atomic_text(cache, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    with (output / "global-terms.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["canonical", "variants", "confidence"]); writer.writeheader()
        for term in record["terms"]: writer.writerow({"canonical": term.get("canonical", ""), "variants": " | ".join(term.get("variants", [])), "confidence": term.get("confidence", "")})
    return record["terms"]


def windows(segments: list[dict]) -> list[list[dict]]:
    result, current, start = [], [], None
    for segment in segments:
        if start is None: start = segment["start_ms"]
        if current and segment["end_ms"] - start >= WINDOW_MS:
            result.append(current); current, start = [], segment["start_ms"]
        current.append(segment)
    if current: result.append(current)
    return result


def correct_window(items: list[dict], terms: list[dict]) -> dict[str, dict]:
    first = items[0]["segment_id"]
    path = WORK / f"{first}.json"
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        return {entry["segment_id"]: entry for entry in record["segments"]}
    prompt = """Correct Traditional-Chinese ASR text only. Preserve meaning; do not summarize, add information, split, merge, reorder, or alter segment IDs/timestamps. Apply only clear corrections. Return exactly one object for every input segment with the same segment_id. uncertain_terms must list unresolved terms.\n\nGlobal terminology:\n""" + json.dumps(terms, ensure_ascii=False) + "\n\nSegments:\n" + json.dumps([{ "segment_id": x["segment_id"], "text": x["raw_text"] } for x in items], ensure_ascii=False)
    response = client().models.generate_content(model=MODEL, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=CORRECTION_SCHEMA, temperature=0))
    received = json.loads(response.text).get("segments", [])
    by_id = {entry.get("segment_id"): entry for entry in received}
    final = []
    for item in items:
        answer = by_id.get(item["segment_id"], {})
        text = answer.get("corrected_text") if isinstance(answer.get("corrected_text"), str) else item["raw_text"]
        final.append({"segment_id": item["segment_id"], "corrected_text": text, "uncertain_terms": answer.get("uncertain_terms", []), "fallback_to_raw": item["segment_id"] not in by_id})
    record = {"model": MODEL, "source_start_ms": items[0]["start_ms"], "source_end_ms": items[-1]["end_ms"], "usage_metadata": response.usage_metadata.model_dump(mode="json") if response.usage_metadata else None, "raw_response": response.text, "segments": final}
    atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return {entry["segment_id"]: entry for entry in final}


def timestamp(value: int, sep: str = ",") -> str:
    h, value = divmod(value, 3_600_000); m, value = divmod(value, 60_000); s, ms = divmod(value, 1_000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


def main() -> int:
    source = json.loads((JOB / "subtitles.json").read_text(encoding="utf-8"))
    raw = source["segments"]
    if not raw or any(item["end_ms"] <= item["start_ms"] for item in raw):
        print("CORRECT=FAIL invalid raw subtitle segments")
        return 1
    WORK.mkdir(parents=True, exist_ok=True)
    terms = generate_terms(raw)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda group: correct_window(group, terms), windows(raw)))
    corrected = {key: value for result in results for key, value in result.items()}
    final = []
    for item in raw:
        answer = corrected[item["segment_id"]]
        final.append({**item, "corrected_text": answer["corrected_text"], "text": answer["corrected_text"], "uncertain_terms": answer["uncertain_terms"], "corrected": answer["corrected_text"] != item["raw_text"]})
    payload = {"source": "chirp_3_merged + gemini-3.6-flash segment correction", "model": MODEL, "segment_count": len(final), "corrected_count": sum(item["corrected"] for item in final), "total_duration_ms": final[-1]["end_ms"], "segments": final}
    atomic_text(JOB / "subtitles-corrected.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_text(JOB / "subtitles-corrected.srt", "\n\n".join(f"{i}\n{timestamp(item['start_ms'])} --> {timestamp(item['end_ms'])}\n{item['corrected_text']}" for i, item in enumerate(final, 1)) + "\n")
    atomic_text(JOB / "subtitles-corrected.vtt", "WEBVTT\n\n" + "\n\n".join(f"{timestamp(item['start_ms'], '.')} --> {timestamp(item['end_ms'], '.')}\n{item['corrected_text']}" for item in final) + "\n")
    atomic_text(JOB / "transcript-corrected.txt", "\n".join(item["corrected_text"] for item in final) + "\n")
    atomic_text(JOB / "transcript-corrected.md", "# 校正逐字稿\n\n" + "\n".join(f"[{timestamp(item['start_ms'])[:-4]}] {item['corrected_text']}" for item in final) + "\n")
    print(f"CORRECT=PASS segments={len(final)} changed={payload['corrected_count']} terms={len(terms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
