"""AI text-correction provider abstraction.

Providers implement CorrectionProvider; the registry resolves a provider id
plus an admin-managed profile to a ready client. Capabilities drive what the
UI may offer per provider/model (REALTIME vs BATCH etc.).

Design rules:
- No paid calls happen inside capability/validation paths.
- Batch means the provider's OFFICIAL batch API only; never a local loop.
- API keys never appear in logs, audit payloads, exceptions or responses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ExecutionMode(StrEnum):
    REALTIME = "REALTIME"
    BATCH = "BATCH"


class ProviderId(StrEnum):
    VERTEX = "vertex"
    OPENROUTER = "openrouter"
    MINIMAX = "minimax"


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_realtime: bool
    supports_batch: bool
    supports_native_schema: bool   # native structured output / response schema
    supports_model_listing: bool
    pricing_known: bool
    batch_note: str | None = None  # shown in UI when batch unavailable/limited


@dataclass(frozen=True)
class SegmentCorrection:
    segment_id: str
    corrected_text: str
    uncertain_terms: list[str] = field(default_factory=list)


class ProviderError(RuntimeError):
    """Safe provider error. message must never contain credentials."""

    def __init__(self, kind: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.kind = kind              # auth / rate_limit / invalid_response /
                                      # unreachable / batch_failed / unknown
        self.safe_message = safe_message


def validate_correction_payload(
    raw: Any,
    expected_ids: list[str],
) -> list[SegmentCorrection]:
    """Strict canonical-schema validation shared by ALL providers.

    Response must contain exactly the requested segment ids, in order,
    with no duplicates, no extras, no inventions. Text must be non-empty
    strings. Raises ProviderError('invalid_response') otherwise.
    """
    if not isinstance(raw, list):
        raise ProviderError("invalid_response", "校正結果必須是 JSON 陣列")
    seen: set[str] = set()
    out: list[SegmentCorrection] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ProviderError("invalid_response", f"第 {i} 項不是物件")
        seg_id = item.get("segment_id")
        text = item.get("corrected_text")
        if not isinstance(seg_id, str) or not seg_id:
            raise ProviderError("invalid_response", f"第 {i} 項缺少 segment_id")
        if seg_id in seen:
            raise ProviderError("invalid_response", f"重複的 segment_id: {seg_id}")
        seen.add(seg_id)
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("invalid_response", f"segment {seg_id} 文字空白")
        terms = item.get("uncertain_terms") or []
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            raise ProviderError("invalid_response", f"segment {seg_id} uncertain_terms 格式錯誤")
        out.append(SegmentCorrection(seg_id, text, [str(t) for t in terms]))
    got_ids = [c.segment_id for c in out]
    if got_ids != expected_ids:
        missing = [s for s in expected_ids if s not in seen]
        extra = [s for s in seen if s not in expected_ids]
        detail = []
        if missing:
            detail.append(f"缺少 {len(missing)} 段")
        if extra:
            detail.append(f"多出 {len(extra)} 段")
        raise ProviderError(
            "invalid_response",
            "segment 清單不一致（" + "、".join(detail) + "）— 拒絕寫入",
        )
    return out


# -- canonical prompt ---------------------------------------------------------

PROMPT_VERSION = "corr-v2"

SYSTEM_INSTRUCTION = (
    "你是佛學課程字幕校正助手。只修正錯別字與明顯聽打錯誤，"
    "保持原意、語氣與分段。不要合併、拆分、重排或刪除任何段落。"
    "時間碼不可改動。"
)


def build_user_prompt(segments: list[dict[str, Any]],
                      glossary: list[dict[str, Any]]) -> str:
    import json as _json

    payload = {
        "segments": [
            {"segment_id": s["segment_id"], "text": s["text"]} for s in segments
        ],
        "glossary": glossary,
        "response_format": {
            "array": True,
            "items": {
                "segment_id": "原 segment_id 原樣返回",
                "corrected_text": "校正後文字（長度與原文相近）",
                "uncertain_terms": ["不確定的詞"],
            },
        },
        "rules": [
            "輸出必須是 JSON 陣列，不要包 code fence",
            "segment_id 必須與輸入完全一致且順序相同",
            "不得新增或刪除段落",
        ],
    }
    return (
        f"{SYSTEM_INSTRUCTION}\n\n"
        f"{_json.dumps(payload, ensure_ascii=False)}"
    )
