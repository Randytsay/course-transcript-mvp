"""Immutable original Drive-download → normalized FLAC provenance for V2.

The importer is responsible for independently observing the Drive file ID and
version at download time. This local check proves the downloaded bytes agree
with that observation; it does not fetch or authenticate Drive metadata.
No provider or network requests occur here.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.providers.golden_v2_audio_staging import duration_seconds, file_sha256
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.jobs.golden_v2_ledger import V2Conflict


class SourceLineageError(ValueError):
    pass


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _wire(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def bind_source_lineage(
    *, original_media: Path, normalized_audio: Path,
    drive_observation: dict[str, Any], expected_normalized_sha256: str,
    output_path: Path, duration_tolerance_seconds: float = 0.5,
) -> dict[str, Any]:
    """Fail closed before a cloud upload; never overwrite an existing lineage.

    ``drive_observation`` must come from the read-only Drive importer, captured
    with the downloaded bytes. Any later Drive revision requires a new job.
    """
    if not isinstance(drive_observation, dict):
        raise SourceLineageError('independently captured Drive observation required')
    if drive_observation.get('provider') != 'google_drive':
        raise SourceLineageError('source must have Drive provenance')
    if not isinstance(drive_observation.get('file_id'), str) or not drive_observation['file_id'].strip():
        raise SourceLineageError('Drive file id is required')
    version = drive_observation.get('version')
    if not isinstance(version, (str, int)) or isinstance(version, bool) or not str(version).strip():
        raise SourceLineageError('observed Drive version is required')
    size = drive_observation.get('size_bytes')
    observed_sha = drive_observation.get('downloaded_sha256')
    if type(size) is not int or size <= 0 or not _sha256(observed_sha):
        raise SourceLineageError('invalid downloaded media size or checksum')
    if not _sha256(expected_normalized_sha256):
        raise SourceLineageError('invalid normalized SHA256')
    if not math.isfinite(duration_tolerance_seconds) or duration_tolerance_seconds < 0:
        raise SourceLineageError('invalid duration tolerance')
    original = Path(original_media).resolve(strict=True)
    normalized = Path(normalized_audio).resolve(strict=True)
    if not original.is_file() or not normalized.is_file() or normalized.suffix.lower() != '.flac':
        raise SourceLineageError('original media and normalized FLAC files required')
    if original.stat().st_size != size or file_sha256(original) != observed_sha:
        raise SourceLineageError('original download disagrees with Drive observation')
    if file_sha256(normalized) != expected_normalized_sha256:
        raise SourceLineageError('normalized FLAC disagrees with approved job SHA')
    original_duration = duration_seconds(original)
    normalized_duration = duration_seconds(normalized)
    if abs(original_duration - normalized_duration) > duration_tolerance_seconds:
        raise SourceLineageError('normalization changed media duration')
    manifest = {
        'schema': 'golden-v2-source-lineage-v1',
        'drive_observation': {
            'provider': 'google_drive',
            'file_id': drive_observation['file_id'],
            'version': str(version),
            'size_bytes': size,
            'downloaded_sha256': observed_sha,
        },
        'original_sha256': observed_sha,
        'original_duration_seconds': round(original_duration, 4),
        'normalized_sha256': expected_normalized_sha256,
        'normalized_duration_seconds': round(normalized_duration, 4),
    }
    output_path = Path(output_path)
    wire = _wire(manifest)
    try:
        store_immutable_bytes(output_path, wire)
    except V2Conflict as exc:
        raise SourceLineageError('immutable lineage differs; source/version changed') from exc
    return {'manifest': manifest, 'manifest_sha256': hashlib.sha256(wire).hexdigest()}
