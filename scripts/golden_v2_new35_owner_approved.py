"""Strict one-shot 4-minute Gemini 3.5 Transcribe paid acceptance."""
from __future__ import annotations
import hashlib
import json
import os
import sys
from datetime import datetime,timezone
from decimal import Decimal,ROUND_CEILING

PROJECT="xenon-chain-506409-c3"
BUCKET=PROJECT+"-golden-v2-smoke"
SOURCE_SHA="17db78ae930d28be3de930467ac256166de54fa62bb2e9cd361cdb58587d5621"
BYTES=7680156
WAV_SHA="b199e203f5500f92ea9c2962ca7e20497b94fad737b28775bae7c52480568447"
SOURCE_KEY=f"golden-v2-smoke/{SOURCE_SHA}/clip-16000Hz-mono.wav"
FOLDER=f"golden-v2-smoke/{SOURCE_SHA}/paid/"
PRIMARY_KEY=FOLDER+"primary-gemini38.receipt.json"
CHIRP_KEY=FOLDER+"chirp3.receipt.json"
CLAIM_KEY=FOLDER+"third-gemini35-new-owner-approved-20261013.claim.json"
RECEIPT_KEY=FOLDER+"third-gemini35-new-owner-approved-20261013.receipt.json"
MODEL="gemini-3.5-transcribe"
STAGE_CENTS=200
TOTAL_CENTS=1000
FLOW_REF=("Randytsay/course-transcript-mvp/"
          ".github/workflows/golden-v2-paid-primary-smoke.yml"
          "@refs/heads/codex/golden-v2-goal-completion")

def validate_github_identity():
    expected={
      "GITHUB_REPOSITORY":"Randytsay/course-transcript-mvp",
      "GITHUB_REPOSITORY_ID":"1317518844",
      "GITHUB_REPOSITORY_OWNER_ID":"122517962",
      "GITHUB_REF":"refs/heads/codex/golden-v2-goal-completion",
      "GITHUB_WORKFLOW_REF":FLOW_REF,
      "GITHUB_EVENT_NAME":"push",
    }
    for name,value in expected.items():
        if os.environ.get(name)!=value:
            raise RuntimeError("untrusted GitHub identity: "+name)
    if MODEL!="gemini-3.5-transcribe" or STAGE_CENTS!=200 or TOTAL_CENTS!=1000:
        raise RuntimeError("user's approval was changed")

def save_once(bucket,name,obj):
    payload=json.dumps(obj,ensure_ascii=False,sort_keys=True,
                       separators=(",",":")).encode("utf-8")
    bucket.blob(name).upload_from_string(
        payload,content_type="application/json",
        if_generation_match=0,retry=None,timeout=30)
    return hashlib.sha256(payload).hexdigest()

def read_previous_and_audio(bucket):
    raw=bucket.blob(SOURCE_KEY).download_as_bytes(retry=None,timeout=65)
    if len(raw)!=BYTES or hashlib.sha256(raw).hexdigest()!=WAV_SHA:
        raise RuntimeError("derived four-minute input SHA mismatch")
    proofs=[];total=0
    for uri,model in [(PRIMARY_KEY,"gemini-3.8-flash"),
                      (CHIRP_KEY,"chirp_3")]:
        evidence=bucket.blob(uri).download_as_bytes(retry=None,timeout=30)
        data=json.loads(evidence)
        if (data.get("source_sha256")!=SOURCE_SHA
            or data.get("model")!=model or data.get("status")!="COMPLETE"
            or data.get("aggregate_cap_cents")!=TOTAL_CENTS
            or type(data.get("estimated_cents")) is not int
            or not 0<=data["estimated_cents"]<=200):
            raise RuntimeError("previous stage needs manual verification")
        if model=="chirp_3" and (not data.get("word_time_offsets_verified")
                                 or not data.get("operation_name")):
            raise RuntimeError("Chirp acoustic evidence unverified")
        total+=data["estimated_cents"]
        proofs.append({"model":model,"receipt_sha256":hashlib.sha256(evidence).hexdigest(),
                       "estimated_cents":data["estimated_cents"]})
    if total+STAGE_CENTS>TOTAL_CENTS:
        raise RuntimeError("combined owner-approved cap exceeded")
    return proofs,total

def cents_estimate(usage):
    if not isinstance(usage,dict):
        raise RuntimeError("no token usage evidence")
    i,o=usage.get("prompt_token_count"),usage.get("candidates_token_count")
    if type(i) is not int or type(o) is not int or i<=0 or o<=0:
        raise RuntimeError("unknown model input/output token usage")
    usd=(Decimal(i)*Decimal("2")+Decimal(o)*Decimal("12"))/Decimal(1000000)
    return int((usd*100).to_integral_value(rounding=ROUND_CEILING))

def transcribed_text(resp,raw):
    texts=[]
    for p in getattr(resp,"parts",None) or []:
        a=getattr(p,"audio_transcription",None)
        texts.append(str(getattr(p,"text","") or getattr(a,"text","") or ""))
    if not any(texts):
        for candidate in raw.get("candidates",[]):
            for p in (candidate.get("content") or {}).get("parts",[]):
                a=p.get("audio_transcription") or {}
                texts.append(str(p.get("text") or a.get("text") or ""))
    return "".join(texts).strip() or str(getattr(resp,"text","") or "").strip()

def run():
    validate_github_identity()
    paid=os.environ.get("GOLDEN_V2_NEW35_PAID_ENABLED","").lower()=="true"
    if paid and os.environ.get("GITHUB_RUN_ATTEMPT")!="1":
        raise RuntimeError("paid model attempts must never be rerun")
    from google.cloud import storage
    from google import genai
    from google.genai import types
    if ("audio_transcription_config" not in types.GenerateContentConfig.model_fields
        or not {"mode","word_timestamp"}.issubset(types.AudioTranscriptionConfig.model_fields)):
        raise RuntimeError("isolated genai SDK version lacks 3.5 transcription schema")
    config=types.GenerateContentConfig(
        audio_transcription_config=types.AudioTranscriptionConfig(
            mode="VERBATIM",word_timestamp=False,language_codes=["zh-TW"]))
    client=storage.Client(project=PROJECT)
    bucket=client.bucket(BUCKET)
    raw_audio=bucket.blob(SOURCE_KEY).download_as_bytes(retry=None,timeout=65)
    proofs,prior_cents=read_previous_and_audio(bucket)
    old_claim=json.loads(bucket.blob(FOLDER+"third-gemini35.claim.json").download_as_bytes(retry=None,timeout=30))
    if old_claim.get("status")!="REQUEST_STARTED_NO_RETRY" or old_claim.get("source_sha256")!=SOURCE_SHA:
        raise RuntimeError("Previous claim not frozen as required")
    print("THIRD_INPUT_AND_TWO_PRIOR_CLOUD_RECEIPTS=VERIFIED")
    if not paid:
        print("THIRD_GEMINI35_SDK_VERBATIM_REQUEST=READY")
        print("THIRD_GEMINI35_PAID_CALLS=0")
        return
    claim={
        "schema":"golden-v2-third-one-shot-claim-v1",
        "source_sha256":SOURCE_SHA,"model":MODEL,
        "source_start_end_seconds":[600,840],
        "stage_cap_cents":STAGE_CENTS,"aggregate_cap_cents":TOTAL_CENTS,
        "previous_receipts":proofs,"previous_estimated_cents":prior_cents,
        "approved_by_owner":"new-single-test-user-approved-20261013","old_third_request":"OUTCOME_UNKNOWN_DO_NOT_REPLAY",
        "workflow_ref":FLOW_REF,"github_run_id":os.environ.get("GITHUB_RUN_ID"),
        "started_utc":datetime.now(timezone.utc).isoformat(),
        "status":"REQUEST_STARTED_NO_RETRY",
    }
    save_once(bucket,CLAIM_KEY,claim)
    print("THIRD_GEMINI35_ATOMIC_CLOUD_CLAIM=COMMITTED")
    http=types.HttpOptions(api_version="v1",timeout=180000,
        retry_options=types.HttpRetryOptions(attempts=1))
    try:
        with genai.Client(enterprise=True,project=PROJECT,location="global",
                          http_options=http) as v2:
            response=v2.models.generate_content(
                model=MODEL,
                contents=[types.Part.from_bytes(data=raw_audio,mime_type="audio/wav")],
                config=config,
            )
    except Exception as exc:
        error={"source_sha256":SOURCE_SHA,"model":MODEL,"claim_key":CLAIM_KEY,"type":type(exc).__name__,"status":str(getattr(exc,"status",None))[:60],"code":str(getattr(exc,"code",None))[:60],"outcome":"UNKNOWN_NO_RETRY"}
        save_once(bucket,FOLDER+"third-gemini35-new-owner-approved-20261013.error.json",error)
        print("THIRD35_NEW_ATTEMPT_PROVIDER_ERROR_RECORDED=true")
        raise
    raw=response.model_dump(mode="json",exclude_none=True)
    text=transcribed_text(response,raw)
    reason=(raw.get("candidates") or [{}])[0].get("finish_reason")
    usage=raw.get("usage_metadata") or {}
    try: cents=cents_estimate(usage)
    except (RuntimeError,TypeError,ValueError):cents=None
    passed=(bool(text) and cents is not None and cents<=STAGE_CENTS
            and prior_cents+cents<=TOTAL_CENTS
            and reason in ("STOP","FINISH_REASON_STOP"))
    receipt={
        "schema":"golden-v2-paid-third-verbatim-receipt-v1",
        "source_sha256":SOURCE_SHA,"model":MODEL,
        "claim_key":CLAIM_KEY,"status":"COMPLETE" if passed else "REVIEW_REQUIRED",
        "transcript":text,"text_length":len(text),
        "mode":"VERBATIM","chirp_remains_time_authority":True,
        "finish_reason":reason,"usage_metadata":usage,
        "estimated_cents":cents,"stage_cap_cents":STAGE_CENTS,
        "aggregate_cap_cents":TOTAL_CENTS,
        "previous_receipts":proofs,
        "raw_response":raw,
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
    }
    save_once(bucket,RECEIPT_KEY,receipt)
    print("THIRD_GEMINI35_PROVIDER_RECEIPT=CREATED")
    print("THIRD_GEMINI35_STATUS="+receipt["status"])
    print("THIRD_GEMINI35_CHARS="+str(len(text)))
    print("THIRD_GEMINI35_ESTIMATE_CENTS="+str(cents))
    print("THIRD_GEMINI35_TEXT_SHA256="+hashlib.sha256(text.encode()).hexdigest())
    if not passed:
        raise RuntimeError("third model needs review; not resubmitting")
    print("THIRD_GEMINI35_ONE_SHOT=SUCCESS")

if __name__=="__main__":
    try:run()
    except Exception as exc:
        print("THIRD_GEMINI35_STOP_ERROR_TYPE="+type(exc).__name__,file=sys.stderr)
        print("THIRD_GEMINI35_MANUAL_RECONCILIATION_REQUIRED=true",file=sys.stderr)
        sys.exit(1)
