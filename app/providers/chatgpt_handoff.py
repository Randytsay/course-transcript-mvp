"""Create and ingest a strict Chirp-3 -> ChatGPT subtitle handoff bundle.

ChatGPT corrects text against stable Chirp segment IDs. Chirp word timings and
source segment timing remain immutable evidence. Preferred re-entry contains no
timestamps at all; legacy SRT re-entry is still checked byte-for-structure.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.canonical.golden_rules import GOLDEN_RULESET_VERSION, golden_terms
from app.canonical.defaults import MANTRA_KEY, SCRIPTURE_KEY
from app.canonical.store import active_canonical
from app.subtitles.editor_hardened import parse_srt_strict


DATA_DIR = Path(os.environ.get("COURSE_TRANSCRIPT_DATA_DIR", "/app/data"))
JOB_NAME = os.environ.get("JOB_NAME", "")
JOB = DATA_DIR / "jobs" / JOB_NAME
BUNDLE_DIRNAME = "chatgpt-handoff"


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _srt_time(milliseconds: int, separator: str = ",") -> str:
    hours, remainder = divmod(max(0, int(milliseconds)), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _render_srt(segments: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"{index}\n{_srt_time(item['start_ms'])} --> {_srt_time(item['end_ms'])}\n"
        f"{item['corrected_text']}"
        for index, item in enumerate(segments, 1)
    ) + "\n"


def _render_vtt(segments: list[dict[str, Any]]) -> str:
    rows = ["WEBVTT", ""]
    for index, item in enumerate(segments, 1):
        rows.extend(
            [
                str(index),
                f"{_srt_time(item['start_ms'], '.')} --> {_srt_time(item['end_ms'], '.')}",
                str(item["corrected_text"]),
                "",
            ]
        )
    return "\n".join(rows)


def write_bundle(
    job_dir: Path,
    *,
    content_mode: str,
    document_context: str = "",
) -> dict[str, Any]:
    raw_json = job_dir / "subtitles.json"
    raw_srt = job_dir / "subtitles.srt"
    merged_words = job_dir / "merged-words.json"
    for path in (raw_json, raw_srt, merged_words):
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"ChatGPT handoff requires {path.name}")

    payload = json.loads(raw_json.read_text(encoding="utf-8"))
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("ChatGPT handoff requires non-empty subtitle segments")

    bundle = job_dir / BUNDLE_DIRNAME
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copy2(raw_srt, bundle / "chirp-raw.srt")
    shutil.copy2(raw_json, bundle / "segments.json")
    shutil.copy2(merged_words, bundle / "merged-words.json")
    completeness = job_dir / "chirp-completeness.json"
    if not completeness.is_file():
        raise RuntimeError("ChatGPT handoff requires a passed Chirp completeness gate")
    completeness_payload = json.loads(completeness.read_text(encoding="utf-8"))
    if not isinstance(completeness_payload, dict) or completeness_payload.get("status") != "PASS":
        raise RuntimeError("ChatGPT handoff is blocked until Chirp completeness passes")
    shutil.copy2(completeness, bundle / "chirp-completeness.json")
    raw_text = "\n".join(
        str(item.get("raw_text") or item.get("text") or "").strip()
        for item in segments
        if isinstance(item, dict)
    ).strip() + "\n"
    _atomic_text(bundle / "raw-transcript.txt", raw_text)

    dacheng = str(content_mode).strip().lower() == "dacheng_buddhist"
    golden_payload = {
        "ruleset_version": GOLDEN_RULESET_VERSION if dacheng else None,
        "terms": golden_terms() if dacheng else [],
        "policy": (
            "reference_only_when_audio_asr_context_supports_it; never blind replace"
            if dacheng
            else "not_applicable"
        ),
    }
    _atomic_json(bundle / "golden-rules.json", golden_payload)
    canonical_payload: dict[str, Any] = {
        "content_mode": content_mode,
        "scripture": None,
        "mantra": None,
    }
    if dacheng:
        for label, key in (("scripture", SCRIPTURE_KEY), ("mantra", MANTRA_KEY)):
            active = active_canonical(DATA_DIR, key)
            if isinstance(active, dict):
                canonical_payload[label] = {
                    "document_key": key,
                    "version": active.get("version"),
                    "title": active.get("title"),
                    "body_text": active.get("body_text"),
                    "checksum": active.get("checksum"),
                    "source": active.get("source"),
                }
    _atomic_json(bundle / "canonical-context.json", canonical_payload)
    instructions = (
        "Treat Chirp 3 word timestamps and source segment timing as immutable evidence.\n"
        "Preferred return format is a complete list of {segment_id, corrected_text}; do not "
        "return or invent timestamps. Preserve every source segment_id exactly once and in order.\n"
        "Use the user-provided reference transcript as spelling/context evidence, not as "
        "permission to overwrite audio evidence or fill audio that Chirp did not recognize.\n"
        "Display cue merging/splitting is a separate deterministic rendering concern and must "
        "never be driven by model-invented timestamps. Legacy SRT return remains supported only "
        "when cue count and every source timestamp are unchanged.\n"
    )
    _atomic_text(bundle / "INSTRUCTIONS.txt", instructions)

    manifest = {
        "schema_version": 1,
        "generated_at": _iso(),
        "job_id": job_dir.name,
        "workflow_mode": "CHATGPT_HANDOFF",
        "content_mode": content_mode,
        "document_context": document_context,
        "cue_count": len(segments),
        "timestamps_immutable": True,
        "reference_transcript_expected_from_user": True,
        "chirp_completeness_status": "PASS",
        "preferred_import_format": "segment_edits",
        "golden_ruleset_version": GOLDEN_RULESET_VERSION if dacheng else None,
        "artifacts": {
            name: {
                "bytes": (bundle / name).stat().st_size,
                "sha256": _sha256(bundle / name),
            }
            for name in (
                "chirp-raw.srt",
                "segments.json",
                "merged-words.json",
                "chirp-completeness.json",
                "raw-transcript.txt",
                "golden-rules.json",
                "canonical-context.json",
                "INSTRUCTIONS.txt",
            )
        },
    }
    _atomic_json(bundle / "handoff-manifest.json", manifest)
    return manifest


def import_corrected_segments(
    job_dir: Path,
    *,
    segment_edits: list[dict[str, Any]],
    actor: str,
) -> dict[str, Any]:
    source_path = job_dir / "subtitles.json"
    if not source_path.is_file():
        raise ValueError("subtitles.json is missing")
    source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    source_segments = source_payload.get("segments", []) if isinstance(source_payload, dict) else []
    if not isinstance(source_segments, list) or not source_segments:
        raise ValueError("subtitles.json has no segments")
    if len(segment_edits) != len(source_segments):
        raise ValueError(
            f"ChatGPT segment count changed: expected {len(source_segments)}, got {len(segment_edits)}"
        )

    corrected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, (source, edit) in enumerate(zip(source_segments, segment_edits), 1):
        if not isinstance(source, dict) or not isinstance(edit, dict):
            raise ValueError(f"segment edit {index} is invalid")
        source_id = str(source.get("segment_id") or f"seg-{index:04d}")
        edit_id = str(edit.get("segment_id") or "")
        if edit_id != source_id:
            raise ValueError(
                f"ChatGPT segment order/id changed at {index}: expected {source_id}, got {edit_id or '<missing>'}"
            )
        if edit_id in seen:
            raise ValueError(f"duplicate ChatGPT segment_id: {edit_id}")
        seen.add(edit_id)
        corrected_text = str(edit.get("corrected_text") or "").strip()
        if not corrected_text:
            raise ValueError(f"ChatGPT corrected_text is empty for {source_id}")
        raw_text = str(source.get("raw_text") or source.get("text") or "")
        corrected.append(
            {
                **source,
                "segment_id": source_id,
                "start_ms": int(source.get("start_ms", -1)),
                "end_ms": int(source.get("end_ms", -1)),
                "raw_text": raw_text,
                "corrected_text": corrected_text,
                "text": corrected_text,
                "correction_source": "chatgpt_handoff_segment_edits",
            }
        )

    corrected_payload = {
        "source": "chatgpt_handoff_segment_edits",
        "timestamps_immutable": True,
        "source_segment_structure_preserved": True,
        "segments": corrected,
    }
    _atomic_json(job_dir / "subtitles-corrected.json", corrected_payload)
    _atomic_text(job_dir / "subtitles-corrected.srt", _render_srt(corrected))
    _atomic_text(job_dir / "subtitles-corrected.vtt", _render_vtt(corrected))
    _atomic_text(
        job_dir / "transcript-corrected.txt",
        "\n".join(str(item["corrected_text"]).strip() for item in corrected) + "\n",
    )
    digest = hashlib.sha256(
        json.dumps(segment_edits, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    audit = {
        "schema_version": 2,
        "imported_at": _iso(),
        "actor": actor,
        "source": "chatgpt_handoff_segment_edits",
        "cue_count": len(corrected),
        "timestamps_immutable": True,
        "source_segment_structure_preserved": True,
        "segment_edits_sha256": digest,
    }
    _atomic_json(job_dir / BUNDLE_DIRNAME / "import-audit.json", audit)
    return audit


def import_corrected_srt(
    job_dir: Path,
    *,
    srt_text: str,
    actor: str,
) -> dict[str, Any]:
    source_path = job_dir / "subtitles.json"
    if not source_path.is_file():
        raise ValueError("subtitles.json is missing")
    source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    source_segments = source_payload.get("segments", []) if isinstance(source_payload, dict) else []
    if not isinstance(source_segments, list) or not source_segments:
        raise ValueError("subtitles.json has no segments")

    imported, stats = parse_srt_strict(srt_text)
    if len(imported) != len(source_segments):
        raise ValueError(
            f"ChatGPT SRT cue count changed: expected {len(source_segments)}, got {len(imported)}"
        )

    corrected: list[dict[str, Any]] = []
    for index, (source, candidate) in enumerate(zip(source_segments, imported), 1):
        if not isinstance(source, dict):
            raise ValueError(f"source segment {index} is invalid")
        source_start = int(source.get("start_ms", -1))
        source_end = int(source.get("end_ms", -1))
        if int(candidate["start_ms"]) != source_start or int(candidate["end_ms"]) != source_end:
            raise ValueError(
                f"ChatGPT SRT changed timestamp at cue {index}: "
                f"expected {source_start}-{source_end}, "
                f"got {candidate['start_ms']}-{candidate['end_ms']}"
            )
        raw_text = str(source.get("raw_text") or source.get("text") or "")
        corrected_text = str(candidate["corrected_text"])
        corrected.append(
            {
                **source,
                "segment_id": str(source.get("segment_id") or f"seg-{index:04d}"),
                "start_ms": source_start,
                "end_ms": source_end,
                "raw_text": raw_text,
                "corrected_text": corrected_text,
                "text": corrected_text,
                "correction_source": "chatgpt_handoff",
            }
        )

    corrected_payload = {
        "source": "chatgpt_handoff",
        "timestamps_immutable": True,
        "segments": corrected,
    }
    _atomic_json(job_dir / "subtitles-corrected.json", corrected_payload)
    _atomic_text(job_dir / "subtitles-corrected.srt", _render_srt(corrected))
    _atomic_text(job_dir / "subtitles-corrected.vtt", _render_vtt(corrected))
    _atomic_text(
        job_dir / "transcript-corrected.txt",
        "\n".join(str(item["corrected_text"]).strip() for item in corrected) + "\n",
    )
    audit = {
        "schema_version": 1,
        "imported_at": _iso(),
        "actor": actor,
        "source": "chatgpt_handoff",
        "cue_count": len(corrected),
        "timestamps_immutable": True,
        "parse_stats": stats,
        "srt_sha256": hashlib.sha256(srt_text.encode("utf-8")).hexdigest(),
    }
    _atomic_json(job_dir / BUNDLE_DIRNAME / "import-audit.json", audit)
    return audit


def main() -> int:
    manifest = write_bundle(
        JOB,
        content_mode=os.environ.get("CONTENT_MODE", "legacy_unspecified"),
        document_context=os.environ.get("DOCUMENT_CONTEXT", ""),
    )
    print(f"CHATGPT_HANDOFF=READY cues={manifest['cue_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
