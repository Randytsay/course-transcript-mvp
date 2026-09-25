from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from app.canonical.golden_corpus import (
    DEFAULT_CORPUS_RELATIVE_PATH,
    DEFAULT_ERROR_MEMORY_RELATIVE_PATH,
    DEFAULT_MANIFEST_RELATIVE_PATH,
    GOLDEN_CORPUS_SCHEMA_VERSION,
    records_from_reviewed_srt,
    write_error_memory,
    write_jsonl,
)


DATE_RE = re.compile(r"(20\d{6})")
LESSON_RE = re.compile(r"(?:^|lesson)(\d{1,2})", re.IGNORECASE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(path: Path) -> tuple[str, str]:
    date_match = DATE_RE.search(path.name)
    lesson_match = LESSON_RE.search(path.name)
    lesson_date = date_match.group(1) if date_match else ""
    lesson_id = f"{int(lesson_match.group(1)):02d}" if lesson_match else lesson_date
    return lesson_id, lesson_date


def build(input_dir: Path, data_dir: Path) -> dict[str, object]:
    files = sorted(input_dir.glob("*.srt"))
    all_records: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    for path in files:
        lesson_id, lesson_date = _metadata(path)
        if not lesson_date:
            continue
        value = path.read_text(encoding="utf-8-sig")
        digest = _sha256(path)
        records = records_from_reviewed_srt(
            value,
            lesson_id=lesson_id,
            lesson_date=lesson_date,
            source_name=path.name,
            source_sha256=digest,
        )
        all_records.extend(records)
        sources.append(
            {
                "lesson_id": lesson_id,
                "lesson_date": lesson_date,
                "source_name": path.name,
                "sha256": digest,
                "record_count": len(records),
            }
        )

    corpus_path = data_dir / DEFAULT_CORPUS_RELATIVE_PATH
    manifest_path = data_dir / DEFAULT_MANIFEST_RELATIVE_PATH
    error_memory_path = data_dir / DEFAULT_ERROR_MEMORY_RELATIVE_PATH
    write_jsonl(all_records, corpus_path)
    error_memory = write_error_memory(error_memory_path)
    manifest = {
        "schema_version": GOLDEN_CORPUS_SCHEMA_VERSION,
        "mode": "human_reviewed_reference",
        "lesson_count": len(sources),
        "record_count": len(all_records),
        "sources": sources,
        "corpus_path": str(corpus_path),
        "error_memory_path": str(error_memory_path),
        "error_memory_entry_count": int(error_memory["entry_count"]),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.input_dir, args.data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
