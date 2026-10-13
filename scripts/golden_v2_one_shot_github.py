"""Golden V2: owner-approved 240-second first paid primary smoke, no retry.

Triggered ONLY by a dedicated trusted GitHub workflow. Google API credentials are
short-lived OIDC ADC. Source, approval, USD cap and model are constants; the
GCS claim object is created atomically BEFORE a charged provider invocation.
A missing/unknown result must be reconciled; NEVER delete claim or rerun.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING

PROJECT = "xenon-chain-506409-c3"
BUCKET = "xenon-chain-506409-c3-golden-v2-smoke"
SOURCE_SHA = "17db78ae930d28be3de930467ac256166de54fa62bb2e9cd361cdb58587d5621"
ORIGINAL_SHA = "97ad073bfdbab1228d06861b1b96b089a2e8df8d0f368e664c230bb6f86a9ef9"
AUDIO_LENGTH_BYTES = 8241422
MODEL = "gemini-3.8-flash"
SOURCE_KEY = f"golden-v2-smoke/{SOURCE_SHA}/clip.flac"
CLAIM_KEY = f"golden-v2-smoke/{SOURCE_SHA}/paid/primary-gemini38.claim.json"
RECEIPT_KEY = f"golden-v2-smoke/{SOURCE_SHA}/paid/primary-gemini38.receipt.json"
FLOW_REF = ("Randytsay/course-transcript-mvp/"
            ".github/workflows/golden-v2-paid-primary-smoke.yml"
            "@refs/heads/codex/golden-v2-goal-completion")
MAX_TOTAL_CENTS = 1000
STAGE_RESERVED_CENTS = 200
TRANSCRIPT_PROMPT = (
    "你是佛學講座繁體中文逐字稿整理員。請只聽取提供的整段四分鐘音訊，"
    "從開頭到結尾忠實逐字轉寫繁體中文。保留重要專有名詞與語助詞，"
    "用正確標點及自然分段。遇到不清楚處標記〔聽不清〕，"
    "不得自行補寫、縮寫、摘要、翻譯或引用沒有說出的經文。"
    "只輸出逐字稿，不要額外說明。"
)

def context_is_exact() -> None:
    checks = {
        "GITHUB_REPOSITORY": "Randytsay/course-transcript-mvp",
        "GITHUB_REPOSITORY_ID": "1317518844",
        "GITHUB_REPOSITORY_OWNER_ID": "122517962",
        "GITHUB_REF": "refs/heads/codex/golden-v2-goal-completion",
        "GITHUB_WORKFLOW_REF": FLOW_REF,
    }
    for key, expected in checks.items():
        if os.environ.get(key) != expected:
            raise RuntimeError(f"unauthorized GitHub OIDC claim: {key}")
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        raise RuntimeError("only dedicated branch push may initiate this trial")
    if (MAX_TOTAL_CENTS != 1000 or STAGE_RESERVED_CENTS != 200
            or MODEL != "gemini-3.8-flash"):
        raise RuntimeError("user approval scope changed")

def record_once(bucket, key: str, obj: dict) -> None:
    # No retries. A 412/timeout means the outcome is ambiguous or already
    # reserved: stop, never interpret it as permission for another request.
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    bucket.blob(key).upload_from_string(
        raw, content_type="application/json",
        if_generation_match=0, retry=None, timeout=30,
    )

def verify_clip(bucket) -> str:
    raw = bucket.blob(SOURCE_KEY).download_as_bytes(retry=None, timeout=60)
    if (len(raw) != AUDIO_LENGTH_BYTES
            or hashlib.sha256(raw).hexdigest() != SOURCE_SHA):
        raise RuntimeError("GCS exact derived 240-second source SHA mismatch")
    return f"gs://{BUCKET}/{SOURCE_KEY}"

def token_cost_cents(usage: dict) -> int:
    i, out = usage.get("prompt_token_count"), usage.get("candidates_token_count")
    thoughts = usage.get("thoughts_token_count", 0)
    if any(type(n) is not int or n < 0 for n in (i, out, thoughts)):
        raise RuntimeError("provider usage unavailable: freeze request")
    if i == 0 or out + thoughts == 0:
        raise RuntimeError("incomplete token usage: freeze request")
    # Conservative public standard (not half-price Flex/Batch) Gemini 3.8 Flash
    # global through Dec 31 2026, USD / million tokens, includes reasoning.
    usd = ((Decimal(i) * Decimal("0.75") +
            Decimal(out + thoughts) * Decimal("3.75")) / Decimal(1000000))
    cents = int((usd * 100).to_integral_value(rounding=ROUND_CEILING))
    return cents

def run() -> None:
    context_is_exact()
    paid = os.environ.get("GOLDEN_V2_PAID_ENABLED", "").lower() == "true"
    if paid and os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise RuntimeError("never rerun a one-shot paid GitHub Action")
    from google.cloud import storage
    # Uses ephemeral GitHub OIDC-issued ADC; never import/copy V1 keys.
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)
    uri = verify_clip(bucket)
    print("GCS_240_SECOND_AUDIO_SHA=VERIFIED")
    if not paid:
        print("PAID_PRIMARY_PREVIEW_ONLY=PASS")
        print("MODEL_INFERENCE_CALLS=0")
        return

    # Freeze consented request identity and both cost boundaries before POST.
    claim = {
        "schema": "golden-v2-one-shot-paid-primary-1",
        "source_sha256": SOURCE_SHA, "parent_source_sha256": ORIGINAL_SHA,
        "source_excerpt_original_seconds": [600, 840],
        "model": MODEL, "owner_approval": "chat-20261013-four-minute-USD10",
        "stage_cap_cents": STAGE_RESERVED_CENTS,
        "aggregate_cap_cents": MAX_TOTAL_CENTS,
        "start_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "workflow_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "flow_ref": FLOW_REF, "gcs_source_uri": uri,
        "status": "REQUEST_STARTED_NO_RETRY",
    }
    record_once(bucket, CLAIM_KEY, claim)
    print("PAID_PRIMARY_CLOUD_CAS_CLAIM=COMMITTED")
    # Every failure from this point freezes the claim. Never reissue POST.
    from google import genai
    from google.genai import types
    http = types.HttpOptions(api_version="v1", timeout=165000,
                             retry_options=types.HttpRetryOptions(attempts=1))
    with genai.Client(enterprise=True, project=PROJECT, location="global",
                      http_options=http) as model_client:
        response = model_client.models.generate_content(
            model=MODEL,
            contents=[TRANSCRIPT_PROMPT,
                      types.Part.from_uri(file_uri=uri, mime_type="audio/flac")],
            config=types.GenerateContentConfig(
                max_output_tokens=8192,
                thinking_config=types.ThinkingConfig(thinking_level="LOW"),
            ),
        )
    raw = response.model_dump(mode="json", exclude_none=True)
    usage = raw.get("usage_metadata")
    text_value = str(response.text or "").strip()
    candidates = raw.get("candidates") or []
    reason = candidates[0].get("finish_reason") if candidates else None
    try:
        cents = token_cost_cents(usage or {})
    except (RuntimeError, TypeError, ValueError):
        cents = None
    passed = (cents is not None and cents <= STAGE_RESERVED_CENTS
              and bool(text_value) and reason in ("STOP", "FINISH_REASON_STOP"))
    receipt = {
        "schema": "golden-v2-paid-primary-provider-response-1",
        "source_sha256": SOURCE_SHA, "model": MODEL,
        "parent_source_sha256": ORIGINAL_SHA,
        "claim_key": CLAIM_KEY,
        "claim_identity_sha256": hashlib.sha256(
            json.dumps(claim, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode()).hexdigest(),
        "status": "COMPLETE" if passed else "REVIEW_REQUIRED",
        "estimated_cents": cents, "stage_cap_cents": STAGE_RESERVED_CENTS,
        "aggregate_cap_cents": MAX_TOTAL_CENTS,
        "model_text": text_value, "finish_reason": reason,
        "usage_metadata": usage,
        "raw_response": raw,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    record_once(bucket, RECEIPT_KEY, receipt)
    print("PAID_PRIMARY_IMMUTABLE_RECEIPT=CREATED")
    print("PAID_PRIMARY_MODEL="+MODEL)
    print("PAID_PRIMARY_STATUS="+receipt["status"])
    print("PAID_PRIMARY_ESTIMATE_CENTS="+str(cents))
    print("PAID_PRIMARY_TRANSCRIPT_SHA256="+hashlib.sha256(text_value.encode()).hexdigest())
    if not passed:
        raise RuntimeError("paid provider response requires reconciliation: no retry")
    print("PAID_PRIMARY_ONE_SHOT=SUCCESS")

if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # Never print raw provider response, user transcript, ADC, bearer token.
        print("PAID_PRIMARY_STOP_ERROR_TYPE="+type(exc).__name__, file=sys.stderr)
        print("MANUAL_RECONCILIATION_REQUIRED=true",file=sys.stderr)
        sys.exit(1)
