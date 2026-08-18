"""Generate citation-backed study packs from owner-approved immutable subtitles.

Generation is an explicit owner action. Learner reads never trigger a paid model
request. Source timestamps are reconstructed server-side from the immutable
subtitle snapshot rather than trusted from the model response.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.review.store import ReviewConflict, ReviewNotFound

from .source import LearningSourceStore
from .store import LearningStore

PROMPT_VERSION = "learning-study-pack-v1"
ARTIFACT_TYPE = "study_pack"


class LearningGenerationError(RuntimeError):
    pass


def _model_name() -> str:
    return (
        os.environ.get("LEARNING_ARTIFACT_MODEL")
        or os.environ.get("VERTEX_MODEL")
        or "gemini-3.7-flash"
    ).strip()


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    candidates = getattr(response, "candidates", None) or []
    pieces: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            value = getattr(part, "text", None)
            if isinstance(value, str):
                pieces.append(value)
    return "\n".join(pieces).strip()


def _vertex_json(prompt: str, *, model: str) -> dict[str, Any]:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global").strip() or "global"
    if not project:
        raise LearningGenerationError("GOOGLE_CLOUD_PROJECT is not configured")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(vertexai=True, project=project, location=location)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=16384,
                temperature=0.2,
            ),
        )
    except Exception as exc:
        raise LearningGenerationError("AI learning-content generation failed") from exc
    raw = _response_text(response)
    if not raw:
        raise LearningGenerationError("AI learning-content generation returned no content")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LearningGenerationError("AI learning-content generation returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise LearningGenerationError("AI learning-content generation returned an invalid payload")
    return payload


def _source_segments(version: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(str(version["snapshot_json"]))
    except (KeyError, json.JSONDecodeError) as exc:
        raise LearningGenerationError("Subtitle version snapshot is invalid") from exc
    if not isinstance(payload, list) or not payload:
        raise LearningGenerationError("Subtitle version contains no segments")
    result: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            segment_index = int(item["segment_index"])
            start_ms = int(item["start_ms"])
            end_ms = int(item["end_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(item.get("working_text") or item.get("original_text") or "").strip()
        if segment_index < 1 or start_ms < 0 or end_ms <= start_ms or not text:
            continue
        result.append(
            {
                "segment_index": segment_index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
            }
        )
    if not result:
        raise LearningGenerationError("Subtitle version contains no usable segments")
    return result


def _prompt(*, title: str, version_number: int, segments: list[dict[str, Any]]) -> str:
    source = "\n".join(f"[{item['segment_index']}] {item['text']}" for item in segments)
    return f"""你是佛學課程學習整理助手。請只根據下方已由管理員核定為正式學習來源、不可變的字幕版本整理內容，不得補充字幕沒有出現的教義、人物、經典內容或外部知識。

課程：{title}
正式學習字幕版本：v{version_number}

輸出必須是單一 JSON 物件，不要 Markdown code fence。所有需要來源的項目都必須提供 source_segment_indexes；只能填下方實際存在的段落編號。若來源不足，寧可省略，不要猜。

JSON 結構：
{{
  "overview": {{"title": "", "summary": "", "source_segment_indexes": [1]}},
  "detailed_notes": [{{"heading": "", "points": [""], "source_segment_indexes": [1]}}],
  "quick_review_10m": [{{"heading": "", "summary": "", "source_segment_indexes": [1]}}],
  "quick_review_3m": [{{"text": "", "source_segment_indexes": [1]}}],
  "key_points": [{{"text": "", "source_segment_indexes": [1]}}],
  "qa": [{{"question": "", "answer": "", "source_segment_indexes": [1]}}],
  "flashcards": [{{"id": "card-1", "front": "", "back": "", "source_segment_indexes": [1]}}],
  "quiz": [{{"id": "quiz-1", "question": "", "choices": ["", "", "", ""], "answer_index": 0, "explanation": "", "source_segment_indexes": [1]}}],
  "glossary": [{{"term": "", "explanation": "", "source_segment_indexes": [1]}}]
}}

品質要求：
- detailed_notes 要保留課程脈絡，不只列關鍵字。
- quick_review_10m 約可在 10 分鐘內複習完。
- quick_review_3m 只保留最重要內容。
- key_points 優先整理 5–12 個真正重要重點。
- qa 使用學員可能會問的自然問題。
- flashcards 盡量一張一個概念，避免過長。
- quiz 以理解為主，不出刁鑽題；answer_index 為 0-based。
- glossary 只整理字幕中確實出現且適合複習的名詞。
- 不要把字幕辨識可能有疑義的句子自行延伸成結論。

已核定字幕：
{source}
"""


def _indexes(value: Any, valid: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for raw in value:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if index in valid and index not in seen:
            seen.add(index)
            result.append(index)
        if len(result) >= 24:
            break
    return result


def _normalize_pack(payload: dict[str, Any], segments: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    valid_map = {int(item["segment_index"]): item for item in segments}
    valid = set(valid_map)
    allowed_lists = {
        "detailed_notes",
        "quick_review_10m",
        "quick_review_3m",
        "key_points",
        "qa",
        "flashcards",
        "quiz",
        "glossary",
    }
    overview = payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
    normalized: dict[str, Any] = {
        "overview": {
            "title": str(overview.get("title") or "").strip()[:300],
            "summary": str(overview.get("summary") or "").strip()[:6000],
            "source_segment_indexes": _indexes(overview.get("source_segment_indexes"), valid),
        }
    }
    all_indexes: set[int] = set(normalized["overview"]["source_segment_indexes"])
    for key in allowed_lists:
        raw_items = payload.get(key)
        items: list[dict[str, Any]] = []
        if isinstance(raw_items, list):
            for raw in raw_items[:100]:
                if not isinstance(raw, dict):
                    continue
                item: dict[str, Any] = {}
                for field in (
                    "id", "heading", "text", "summary", "question", "answer",
                    "explanation", "term", "front", "back",
                ):
                    if field in raw:
                        item[field] = str(raw.get(field) or "").strip()[:6000]
                if isinstance(raw.get("points"), list):
                    item["points"] = [
                        str(value).strip()[:2000]
                        for value in raw["points"][:30]
                        if str(value).strip()
                    ]
                if isinstance(raw.get("choices"), list):
                    item["choices"] = [str(value).strip()[:1000] for value in raw["choices"][:8]]
                if "answer_index" in raw:
                    try:
                        item["answer_index"] = int(raw["answer_index"])
                    except (TypeError, ValueError):
                        item["answer_index"] = 0
                indexes = _indexes(raw.get("source_segment_indexes"), valid)
                if not indexes:
                    continue
                item["source_segment_indexes"] = indexes
                all_indexes.update(indexes)
                items.append(item)
        normalized[key] = items

    citations = [
        {
            "segment_index": index,
            "start_ms": int(valid_map[index]["start_ms"]),
            "end_ms": int(valid_map[index]["end_ms"]),
            "text": str(valid_map[index]["text"]),
        }
        for index in sorted(all_indexes)
    ]
    if not normalized["key_points"] and not normalized["detailed_notes"]:
        raise LearningGenerationError("AI learning-content generation did not return supported study notes")
    return normalized, citations


def generate_study_pack(
    store: LearningStore,
    *,
    youtube_video_id: str,
    actor: str,
    force: bool = False,
) -> dict[str, Any]:
    source_store = LearningSourceStore(store.database_path)
    source = source_store.require(youtube_video_id)
    source_status = source_store.status(youtube_video_id)
    if not source_status["source_is_latest"]:
        raise ReviewConflict(
            "The formal learning source is older than the latest subtitle version; approve the latest version before generating AI learning content"
        )

    with store.connect() as connection:
        video = connection.execute(
            "SELECT * FROM review_videos WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
    if video is None:
        raise ReviewNotFound("Learning video not found")

    version = {
        "id": source["subtitle_version_id"],
        "version_number": source["version_number"],
        "content_sha256": source["content_sha256"],
        "snapshot_json": source["snapshot_json"],
    }
    existing = store.artifact_for_video(youtube_video_id, artifact_type=ARTIFACT_TYPE)
    if (
        existing
        and not force
        and str(existing["source_sha256"]) == str(version["content_sha256"])
        and str(existing["prompt_version"]) == PROMPT_VERSION
    ):
        return {"artifact": existing, "generated": False, "reason": "current artifact already exists"}

    model = _model_name()
    job = store.begin_generation_job(
        youtube_video_id=youtube_video_id,
        subtitle_version_id=str(version["id"]),
        artifact_type=ARTIFACT_TYPE,
        prompt_version=PROMPT_VERSION,
        model=model,
        actor=actor,
    )
    try:
        segments = _source_segments(version)
        payload = _vertex_json(
            _prompt(
                title=str(video["title"]),
                version_number=int(version["version_number"]),
                segments=segments,
            ),
            model=model,
        )
        content, citations = _normalize_pack(payload, segments)
        store.store_artifact(
            youtube_video_id=youtube_video_id,
            subtitle_version_id=str(version["id"]),
            source_sha256=str(version["content_sha256"]),
            artifact_type=ARTIFACT_TYPE,
            title=f"{video['title']}｜AI 學習整理",
            content=content,
            citations=citations,
            model=model,
            prompt_version=PROMPT_VERSION,
            actor=actor,
        )
        store.finish_generation_job(str(job["id"]))
        return {
            "artifact": store.artifact_for_video(youtube_video_id, artifact_type=ARTIFACT_TYPE),
            "generated": True,
        }
    except Exception as exc:
        try:
            store.finish_generation_job(str(job["id"]), error=str(exc))
        except Exception:
            pass
        if isinstance(exc, (LearningGenerationError, ReviewConflict, ReviewNotFound)):
            raise
        raise LearningGenerationError("AI learning-content generation failed") from exc
