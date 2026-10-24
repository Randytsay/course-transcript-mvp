"""Independent machine-reviewed V2 quality/publication contract (no I/O).

AI_REVIEWED is never GOLDEN_PASS. This module only validates bound evidence and
a prospective publication grant. It does not mint owner grants, claim human
listening, generate missing words, or mutate Drive. A delivery controller must
verify these hashes against the ledger and immutable source artifacts before
calling these validators and must require a separate signed owner grant.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.jobs.golden_v2_ledger import V2Conflict

SCHEMA = "golden-v2-ai-review-contract-v1"
QUALITY_TIER = "AI_REVIEWED_NOT_GOLDEN"
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def build_ai_review_certificate(
    *, job_id: str, source_sha256: str, chirp_sha256: str,
    acoustic_qa_sha256: str, render_sha256: str, report: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed unless every explicit machine QA gate is satisfied.

    Inputs must be read from the immutable, ledger-bound artifacts by the
    trusted caller. An incomplete/uncertain interval is not 'silence'.
    """
    if (not re.fullmatch(r"v2p_[0-9a-f]{32}", job_id or "")
            or not all(_sha(v) for v in (
                source_sha256, chirp_sha256, acoustic_qa_sha256, render_sha256))
            or not isinstance(report, dict)):
        raise V2Conflict("invalid bound AI quality evidence")
    expected = {"source_sha256": source_sha256,
                "chirp_sha256": chirp_sha256,
                "acoustic_qa_sha256": acoustic_qa_sha256,
                "render_sha256": render_sha256}
    if any(report.get(k) != v for k, v in expected.items()):
        raise V2Conflict("AI review evidence does not match source/Chirp/render")
    if (report.get("acoustic_gate") != "PASS"
            or report.get("timeline_gate") != "PASS"
            or report.get("srt_txt_consistency") != "PASS"
            or report.get("semantic_gate") != "PASS"):
        raise V2Conflict("machine quality checks not all PASS")
    fields = ("unverified_audio_intervals", "missing_windows",
              "unresolved_semantic_conflicts", "unverified_srt_cues")
    if any(type(report.get(field)) is not int or report[field] != 0
           for field in fields):
        raise V2Conflict("unresolved review items preclude AI-reviewed certificate")
    if (type(report.get("cue_count")) is not int or report["cue_count"] <= 0
            or type(report.get("word_count")) is not int
            or report["word_count"] <= 0
            or report.get("human_listened") is True):
        raise V2Conflict("AI review cannot assert missing or human evidence")
    cert = {
        "schema": SCHEMA, "quality_tier": QUALITY_TIER,
        "job_id": job_id, **expected,
        "cue_count": report["cue_count"], "word_count": report["word_count"],
        "machine_quality_status": "PASS",
        "human_listening": "NOT_CLAIMED",
        "golden_acceptance": "NOT_CLAIMED",
        "delivery_authorized": False,
    }
    cert["certificate_sha256"] = hashlib.sha256(
        json.dumps(cert, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    return cert


def validate_ai_publication_intent(
    *, certificate: dict[str, Any], grant: dict[str, Any],
    filenames: list[str], existing_target_count: int,
) -> None:
    """Validate an independently authenticated *proposal*, never approve it.

    The caller must first validate the grant's HMAC with a trusted secret,
    verify the ledger revision and the certificate bytes, and then validate
    Drive source/target freshness under an exclusive publication lock.
    """
    if (not isinstance(certificate, dict)
            or certificate.get("schema") != SCHEMA
            or certificate.get("quality_tier") != QUALITY_TIER
            or certificate.get("machine_quality_status") != "PASS"
            or certificate.get("human_listening") != "NOT_CLAIMED"
            or certificate.get("golden_acceptance") != "NOT_CLAIMED"
            or certificate.get("delivery_authorized") is not False):
        raise V2Conflict("AI certificate is not eligible")
    if (not isinstance(grant, dict)
            or grant.get("quality_tier") != QUALITY_TIER
            or grant.get("job_id") != certificate.get("job_id")
            or grant.get("source_sha256") != certificate.get("source_sha256")
            or grant.get("certificate_sha256") != certificate.get("certificate_sha256")
            or grant.get("render_sha256") != certificate.get("render_sha256")
            or grant.get("owner_confirm") is not True
            or not isinstance(grant.get("actor"), str)
            or not grant["actor"].strip()
            or grant.get("confirm_listened") is not None
            or type(grant.get("revision")) is not int):
        raise V2Conflict("separate signed AI delivery intent required")
    if (type(existing_target_count) is not int
            or existing_target_count != 0
            or not isinstance(filenames, list) or len(filenames) != 2
            or not all(isinstance(n, str) for n in filenames)):
        raise V2Conflict("AI delivery targets must be new and nonconflicting")
    srt, transcript = filenames
    if (not srt.endswith("_AI校對.srt")
            or transcript != srt[:-4] + ".txt"
            or "/" in srt or "\\" in srt
            or len(srt) > 180
            or grant.get("filenames") != filenames):
        raise V2Conflict("AI deliverables must use distinct matched AI labels")
