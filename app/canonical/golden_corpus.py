from __future__ import annotations

"""Deterministic retrieval over human-reviewed Dacheng transcript cues.

The corpus is deliberately local/file-backed. It does not train or call a
provider model. It supplies bounded human-approved historical examples to the
text-correction prompt while preserving the priority of current audio,
current-scripture context, segment IDs, and timestamps.
"""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from app.canonical.defaults import MANTRA_LINES, MANTRA_TITLE
from app.canonical.golden_rules import golden_terms


GOLDEN_CORPUS_SCHEMA_VERSION = 1
DEFAULT_CORPUS_RELATIVE_PATH = Path("canonical") / "golden-corpus.jsonl"
DEFAULT_MANIFEST_RELATIVE_PATH = Path("canonical") / "golden-corpus-manifest.json"
DEFAULT_ERROR_MEMORY_RELATIVE_PATH = Path("canonical") / "golden-error-memory.json"


def normalize_text(value: str) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", value or "").lower()


def _ngrams(value: str, size: int = 2) -> set[str]:
    normalized = normalize_text(value)
    if not normalized:
        return set()
    if len(normalized) < size:
        return {normalized}
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_timestamp(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.replace(".", ",").split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )


def parse_srt_text(value: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", value.strip()):
        lines = [line.rstrip() for line in block.splitlines()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        left, right = [part.strip() for part in lines[1].split("-->", 1)]
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue
        cues.append(
            {
                "start_ms": _parse_timestamp(left),
                "end_ms": _parse_timestamp(right),
                "text": text,
            }
        )
    return cues


def _stage_for_text(text: str) -> str:
    stripped = text.strip()
    if stripped == MANTRA_TITLE or stripped in MANTRA_LINES:
        return "mantra"
    if "得見彌勒根本大明神咒" in stripped:
        return "mantra"
    return "lesson"


def records_from_reviewed_srt(
    value: str,
    *,
    lesson_id: str,
    lesson_date: str,
    source_name: str,
    source_sha256: str | None = None,
) -> list[dict[str, Any]]:
    cues = parse_srt_text(value)
    records: list[dict[str, Any]] = []
    digest = source_sha256 or _sha256_text(value)
    for index, cue in enumerate(cues):
        text = str(cue["text"]).strip()
        normalized = normalize_text(text)
        if len(normalized) < 2:
            continue
        prior = str(cues[index - 1]["text"]).strip() if index > 0 else ""
        following = str(cues[index + 1]["text"]).strip() if index + 1 < len(cues) else ""
        context_text = " ".join(part for part in (prior, text, following) if part)
        record_id = _sha256_text(
            f"{lesson_id}\n{cue['start_ms']}\n{cue['end_ms']}\n{text}"
        )[:24]
        records.append(
            {
                "schema_version": GOLDEN_CORPUS_SCHEMA_VERSION,
                "record_id": record_id,
                "lesson_id": lesson_id,
                "lesson_date": lesson_date,
                "source_name": source_name,
                "source_sha256": digest,
                "start_ms": int(cue["start_ms"]),
                "end_ms": int(cue["end_ms"]),
                "text": text,
                "context_text": context_text,
                "normalized_text": normalized,
                "stage": _stage_for_text(text),
                "approval": "human_reviewed",
            }
        )
    return records


def write_jsonl(records: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("text"):
                records.append(value)
    return records


def corpus_digest(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class _IndexedRecord:
    record: dict[str, Any]
    grams: frozenset[str]


class GoldenCorpusIndex:
    def __init__(self, records: Iterable[dict[str, Any]]) -> None:
        self._records: list[_IndexedRecord] = []
        self._postings: dict[str, set[int]] = {}
        for raw in records:
            text = str(raw.get("context_text") or raw.get("text") or "")
            grams = frozenset(_ngrams(text))
            if not grams:
                continue
            index = len(self._records)
            self._records.append(_IndexedRecord(record=dict(raw), grams=grams))
            for gram in grams:
                self._postings.setdefault(gram, set()).add(index)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 4,
        min_score: float = 0.18,
        exclude_stage: str | None = "mantra",
    ) -> list[dict[str, Any]]:
        qgrams = _ngrams(query)
        if not qgrams:
            return []
        candidate_ids: set[int] = set()
        for gram in qgrams:
            candidate_ids.update(self._postings.get(gram, set()))
        scored: list[tuple[float, str, int, dict[str, Any]]] = []
        qnorm = normalize_text(query)
        for index in candidate_ids:
            item = self._records[index]
            record = item.record
            if exclude_stage and str(record.get("stage")) == exclude_stage:
                continue
            overlap = len(qgrams.intersection(item.grams))
            if not overlap:
                continue
            score = (2.0 * overlap) / (len(qgrams) + len(item.grams))
            rnorm = str(record.get("normalized_text") or normalize_text(str(record.get("text") or "")))
            if rnorm and (rnorm in qnorm or qnorm in rnorm):
                score += 0.18
            if score < min_score:
                continue
            scored.append(
                (
                    score,
                    str(record.get("lesson_date") or ""),
                    int(record.get("start_ms") or 0),
                    record,
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [
            {
                **record,
                "score": round(score, 4),
            }
            for score, _, _, record in scored[: max(1, top_k)]
        ]


@lru_cache(maxsize=8)
def _load_index_cached(path_text: str, digest: str) -> GoldenCorpusIndex:
    del digest
    return GoldenCorpusIndex(load_jsonl(Path(path_text)))


def load_index(path: Path) -> GoldenCorpusIndex:
    return _load_index_cached(str(path), corpus_digest(path))


def build_error_memory() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for term in golden_terms():
        canonical = str(term.get("canonical") or "").strip()
        variants = [str(value).strip() for value in term.get("variants", []) if str(value).strip()]
        if not canonical or not variants:
            continue
        lessons = [str(value) for value in term.get("lessons", []) if str(value)]
        entries.append(
            {
                "canonical": canonical,
                "variants": variants,
                "confidence": str(term.get("confidence") or "low"),
                "scope": str(term.get("scope") or ""),
                "lessons": lessons,
                "support_count": len(set(lessons)),
            }
        )
    return {
        "schema_version": 1,
        "mode": "reference_only",
        "entry_count": len(entries),
        "entries": entries,
    }


def write_error_memory(path: Path) -> dict[str, Any]:
    data = build_error_memory()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return data


def error_memory_reference(items: list[dict[str, Any]], *, max_terms: int = 12) -> str:
    query = "\n".join(str(item.get("raw_text") or "") for item in items)
    hits: list[dict[str, Any]] = []
    for entry in build_error_memory()["entries"]:
        matched = [variant for variant in entry["variants"] if variant and variant in query]
        if matched:
            hits.append({**entry, "matched_variants": matched})
    if not hits:
        return ""
    hits.sort(
        key=lambda item: (
            0 if item.get("confidence") == "high" else 1,
            -int(item.get("support_count") or 0),
            str(item.get("canonical") or ""),
        )
    )
    lines = [
        "Historical Error Memory (human-reviewed; reference only):",
        "Use a mapping only when this window's ASR/context supports it. Never force a replacement.",
    ]
    for item in hits[:max_terms]:
        lines.append(
            f"- {', '.join(item['matched_variants'])} → {item['canonical']} "
            f"[{item['confidence']}; {item['scope']}]"
        )
    return "\n".join(lines)


def golden_corpus_reference(
    items: list[dict[str, Any]],
    data_dir: Path,
    *,
    top_k: int = 4,
    max_chars: int = 1800,
) -> str:
    path = data_dir / DEFAULT_CORPUS_RELATIVE_PATH
    if not path.exists():
        return ""
    query = " ".join(str(item.get("raw_text") or "") for item in items)
    retrieval_query = query
    # Expand known ASR variants to their reviewed canonical forms for retrieval
    # only. This never edits the source segment; it merely helps locate the
    # relevant human-approved historical example.
    for term in golden_terms():
        canonical = str(term.get("canonical") or "").strip()
        if not canonical:
            continue
        for variant in term.get("variants", []):
            value = str(variant).strip()
            if value and value in retrieval_query:
                retrieval_query = retrieval_query.replace(value, canonical)
    if len(normalize_text(query)) < 4:
        return ""
    matches = load_index(path).retrieve(retrieval_query, top_k=top_k)
    if not matches:
        return ""
    lines = [
        "Golden Corpus historical examples (human-reviewed; lower priority than current scripture/audio):",
        "Use only when the current ASR meaning and local context support the same wording. "
        "Do not copy unrelated historical text.",
    ]
    for item in matches:
        lesson = str(item.get("lesson_date") or item.get("lesson_id") or "")
        text = str(item.get("text") or "").replace("\n", " ").strip()
        lines.append(f"- [{lesson}; score={item['score']:.3f}] {text}")
        if len("\n".join(lines)) >= max_chars:
            break
    return "\n".join(lines)[:max_chars]
