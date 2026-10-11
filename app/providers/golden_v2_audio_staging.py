"""Local, provider-free audio slicing for Golden Transcript V2.

Only produces private local FLAC clips + a content-addressed manifest. The
original normalized.flac remains unchanged. No GCS, Vertex, Chirp or Drive
calls occur here. Actual GCS publication is a separately authorized phase.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Callable

from app.providers.gemini_primary_audio import (
    MAX_WINDOW_SECONDS, plan_windows, _atomic_json,
)


class AudioStagingError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def duration_seconds(path: Path, *, run: Callable[..., Any] = subprocess.run) -> float:
    result = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True, timeout=45,
    )
    try:
        duration = float(result.stdout.strip())
    except (ValueError, AttributeError) as exc:
        raise AudioStagingError("ffprobe returned invalid duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise AudioStagingError("audio has non-positive or invalid duration")
    return duration


def _validate_existing(path: Path, expected_sha: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise AudioStagingError("missing or empty staged clip")
    if file_sha256(path) != expected_sha:
        raise AudioStagingError("immutable clip differs from manifest")


def prepare_local_clips(
    *, source: Path, expected_source_sha256: str, output_dir: Path,
    max_window_seconds: float = MAX_WINDOW_SECONDS, overlap_seconds: float = 10.0,
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Split a verified immutable 16kHz FLAC; no paid operation or upload.

    Do not call this with untrusted paths. Caller maps a canonical job's
    normalized FLAC to its original Drive/source provenance. Here source SHA
    identifies *normalized FLAC bytes*, not the deleted original media.
    """
    source = source.resolve(strict=True)
    if source.suffix.lower() != ".flac":
        raise AudioStagingError("only normalized FLAC accepted")
    if len(expected_source_sha256) != 64 or file_sha256(source) != expected_source_sha256:
        raise AudioStagingError("normalized source SHA256 does not match preflight")
    seconds = duration_seconds(source, run=run)
    specs = plan_windows(seconds, max_seconds=max_window_seconds, overlap_seconds=overlap_seconds)
    output_dir.mkdir(parents=True, exist_ok=True)
    if source == output_dir.resolve():
        raise AudioStagingError("output directory must differ from source")
    items = []
    for spec in specs:
        index = int(spec["index"])
        start = float(spec["start_seconds"])
        end = float(spec["end_seconds"])
        # Stable names avoid overwriting a different source or offset.
        clip_name = (
            f"{expected_source_sha256[:20]}.{index:04d}."
            f"{int(round(start*1000)):012d}-{int(round(end*1000)):012d}.flac"
        )
        target = output_dir / clip_name
        if target.resolve() == source:
            raise AudioStagingError("cannot overwrite source")
        # Clip artifacts are immutable once created. If present, validate a
        # trusted sidecar hash; never assume a partially written clip is sound.
        sidecar = target.with_suffix(".flac.sha256")
        if target.exists() or sidecar.exists():
            if not target.is_file() or not sidecar.is_file():
                raise AudioStagingError("partial prior clip requires review")
            _validate_existing(target, sidecar.read_text(encoding="ascii").strip())
        else:
            temporary = output_dir / (clip_name + ".partial")
            if temporary.exists():
                raise AudioStagingError("unreconciled partial clip")
            try:
                run(
                    ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                     "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end-start:.3f}",
                     "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac",
                     "-y", "-f", "flac", str(temporary)],
                    check=True, capture_output=True, text=True, timeout=180,
                )
                if not temporary.is_file() or temporary.stat().st_size <= 0:
                    raise AudioStagingError("ffmpeg did not produce clip")
                measured = duration_seconds(temporary, run=run)
                if abs(measured - (end-start)) > 0.20:
                    raise AudioStagingError("clip duration deviates from source window")
                digest = file_sha256(temporary)
                temporary.replace(target)
                sidecar.write_text(digest + "\n", encoding="ascii")
            finally:
                temporary.unlink(missing_ok=True)
        clip_sha = file_sha256(target)
        items.append({
            **spec, "clip_filename": clip_name, "clip_sha256": clip_sha,
            "clip_bytes": target.stat().st_size, "mime_type": "audio/flac",
        })
    manifest = {
        "schema": "golden-v2-local-audio-windows-v1",
        "source_kind": "normalized_flac_sha256",
        "source_sha256": expected_source_sha256,
        "source_duration_seconds": seconds,
        "local_window_count": len(items),
        "window_overlap_requires_reconciliation": overlap_seconds > 0 and len(items) > 1,
        "windows": items,
        "gcs_uploaded": False,
        "paid_model_called": False,
    }
    destination = output_dir / f"windows.{expected_source_sha256[:20]}.json"
    if destination.exists():
        previous = json.loads(destination.read_text(encoding="utf-8"))
        # The ffprobe time should be stable for a fixed immutable normalized FLAC.
        if previous != manifest:
            raise AudioStagingError("existing window manifest differs; fail closed")
    else:
        _atomic_json(destination, manifest)
    return manifest
