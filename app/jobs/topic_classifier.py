"""Deterministic topic classification for Golden Course Transcript intake."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final, Iterable

from app.jobs.content_context import (
    DACHENG_BUDDHIST,
    GENERIC,
    HVAC_ENERGY,
    MARKET_AMERICA_TRAINING,
)

@dataclass(frozen=True)
class TopicProfile:
    name: str
    description_zh: str
    strong_terms: tuple[str, ...]
    weak_terms: tuple[str, ...]
    correction_terms: tuple[str, ...]

PROFILES: Final[dict[str, TopicProfile]] = {
    DACHENG_BUDDHIST: TopicProfile(
        DACHENG_BUDDHIST, "佛教／《佛說彌勒大成佛經》課程",
        ("佛說彌勒大成佛經", "得見彌勒根本大明神咒", "龍華三會"),
        ("彌勒", "阿羅漢", "菩薩", "如來", "兜率", "翅頭末城", "佛經"),
        ("彌勒", "兜率內院", "翅頭末城", "阿耨多羅三藐三菩提"),
    ),
    MARKET_AMERICA_TRAINING: TopicProfile(
        MARKET_AMERICA_TRAINING, "美安／Market America 商品與事業訓練",
        ("market america", "美安超連鎖", "shop.com", "isotonix"),
        ("opc-3", "nad+", "等滲", "ibv", "bv", "ufo", "超連鎖店主", "美安"),
        ("Market America", "SHOP.COM", "Isotonix", "OPC-3", "NAD+", "IBV", "BV", "UFO"),
    ),
    HVAC_ENERGY: TopicProfile(
        HVAC_ENERGY, "HVAC／能源／儲能／EMS 技術課程",
        ("ipmvp", "ashrae", "iso 50001", "iso 50006", "bess"),
        ("hvac", "ems", "冰水主機", "冷卻水", "儲能", "需量", "m&v", "chiller", "modbus", "節能"),
        ("HVAC", "BESS", "EMS", "IPMVP", "ASHRAE", "ISO 50001", "ISO 50006", "M&V", "Modbus"),
    ),
}

_SPACE = re.compile(r"\s+")

def _normalize(value: str) -> str:
    return _SPACE.sub(" ", str(value or "").strip().lower())

def _hits(text: str, terms: Iterable[str]) -> list[str]:
    return [term for term in terms if _normalize(term) in text]

def classify_topic(*, source_name: str = "", folder_name: str = "", document_context: str = "",
                   reference_sample: str = "", chirp_sample: str = "") -> dict[str, object]:
    fields = {
        "source_name": _normalize(source_name),
        "folder_name": _normalize(folder_name),
        "document_context": _normalize(document_context),
        "reference_sample": _normalize(reference_sample[:12000]),
        "chirp_sample": _normalize(chirp_sample[:12000]),
    }
    scores: dict[str, int] = {}
    evidence: dict[str, list[dict[str, object]]] = {}
    for name, profile in PROFILES.items():
        score = 0
        rows: list[dict[str, object]] = []
        for field, text in fields.items():
            if not text:
                continue
            strong = _hits(text, profile.strong_terms)
            weak = _hits(text, profile.weak_terms)
            field_score = min(4, len(strong) * 3) + min(2, len(weak))
            if field_score:
                score += field_score
                rows.append({"source": field, "score": field_score, "strong_hits": strong, "weak_hits": weak})
        scores[name] = score
        evidence[name] = rows
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    winner, winner_score = ordered[0]
    runner_score = ordered[1][1] if len(ordered) > 1 else 0
    conflicting_specialised = runner_score >= 5
    specialised = (
        winner_score >= 5
        and winner_score - runner_score >= 2
        and not conflicting_specialised
    )
    selected = winner if specialised else GENERIC
    confidence = "high" if specialised and winner_score >= 8 and winner_score - runner_score >= 3 else "medium" if specialised else "low"
    return {
        "profile": selected,
        "confidence": confidence,
        "scores": scores,
        "evidence": evidence.get(winner, []) if specialised else [],
        "winner_candidate": winner,
        "winner_score": winner_score,
        "runner_up_score": runner_score,
        "requires_review": bool(winner_score and not specialised),
        "policy": "specialised_only_when_score>=5_and_margin>=2_else_generic",
    }

def profile_context(profile_name: str) -> dict[str, object]:
    if profile_name == GENERIC:
        return {"name": GENERIC, "description_zh": "一般課程／未達專用主題信心門檻", "correction_terms": []}
    profile = PROFILES[profile_name]
    return {"name": profile.name, "description_zh": profile.description_zh, "correction_terms": list(profile.correction_terms)}
