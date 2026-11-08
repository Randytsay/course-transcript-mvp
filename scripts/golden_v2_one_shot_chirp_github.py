"""Approved single four-minute Chirp 3 acoustic timestamp smoke; no retry."""
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
AUDIO_BYTES = 8241422
SOURCE_KEY = f"golden-v2-smoke/{SOURCE_SHA}/clip.flac"
PRIMARY_RECEIPT = f"golden-v2-smoke/{SOURCE_SHA}/paid/primary-gemini38.receipt.json"
CHIRP_CLAIM = f"golden-v2-smoke/{SOURCE_SHA}/paid/chirp3.claim.json"
CHIRP_OP = f"golden-v2-smoke/{SOURCE_SHA}/paid/chirp3.operation.json"
CHIRP_RECEIPT = f"golden-v2-smoke/{SOURCE_SHA}/paid/chirp3.receipt.json"
MODEL = "chirp_3"
STAGE_CAP_CENTS = 200
TOTAL_CAP_CENTS = 1000
WORKFLOW_REF = ("Randytsay/course-transcript-mvp/"
                ".github/workflows/golden-v2-paid-primary-smoke.yml"
                "@refs/heads/codex/golden-v2-goal-completion")

def demand_identity() -> None:
    expected = {
        "GITHUB_REPOSITORY": "Randytsay/course-transcript-mvp",
        "GITHUB_REPOSITORY_ID": "1317518844",
        "GITHUB_REPOSITORY_OWNER_ID": "122517962",
        "GITHUB_REF": "refs/heads/codex/golden-v2-goal-completion",
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "GITHUB_EVENT_NAME": "push",
    }
    for key,value in expected.items():
        if os.environ.get(key)!=value:
            raise RuntimeError("unauthorized workflow claim "+key)
    if TOTAL_CAP_CENTS != 1000 or STAGE_CAP_CENTS != 200:
        raise RuntimeError("approved four-minute budget changed")

def write_once(bucket, key, body):
    payload=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
    bucket.blob(key).upload_from_string(
        payload,content_type="application/json",
        if_generation_match=0,retry=None,timeout=30
    )
    return hashlib.sha256(payload).hexdigest()

def verify_input_and_primary(bucket):
    raw=bucket.blob(SOURCE_KEY).download_as_bytes(retry=None,timeout=60)
    if len(raw)!=AUDIO_BYTES or hashlib.sha256(raw).hexdigest()!=SOURCE_SHA:
        raise RuntimeError("unexpected four-minute input bytes")
    p_raw=bucket.blob(PRIMARY_RECEIPT).download_as_bytes(retry=None,timeout=30)
    receipt=json.loads(p_raw)
    if (receipt.get("status")!="COMPLETE" or
        receipt.get("model")!="gemini-3.8-flash" or
        receipt.get("source_sha256")!=SOURCE_SHA or
        type(receipt.get("estimated_cents")) is not int or
        not 0<=receipt["estimated_cents"]<=200 or
        receipt.get("aggregate_cap_cents")!=TOTAL_CAP_CENTS):
        raise RuntimeError("previous paid primary not independently verified")
    return receipt["estimated_cents"],hashlib.sha256(p_raw).hexdigest()

def duration_seconds(value):
    if hasattr(value,"total_seconds"):
        return float(value.total_seconds())
    if hasattr(value,"seconds") and hasattr(value,"nanos"):
        return float(value.seconds)+float(value.nanos)/1e9
    raise RuntimeError("missing provider-billed duration")

def estimate_cents(seconds):
    if not math.isfinite(seconds) or not 0<seconds<=300:
        raise RuntimeError("unknown Chirp billed duration")
    # Speech V2 Standard regular batching, USD $0.016/min
    cost=Decimal(str(seconds))*Decimal("0.016")/Decimal(60)
    return int((cost*100).to_integral_value(rounding=ROUND_CEILING))

def build_request(uri):
    from google.cloud.speech_v2.types import cloud_speech
    return cloud_speech.BatchRecognizeRequest(
        recognizer=f"projects/{PROJECT}/locations/us/recognizers/_",
        config=cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=["cmn-Hant-TW"],model=MODEL,
            features=cloud_speech.RecognitionFeatures(
                enable_word_time_offsets=True,
                enable_automatic_punctuation=True,
            ),
        ),
        files=[cloud_speech.BatchRecognizeFileMetadata(uri=uri)],
        recognition_output_config=cloud_speech.RecognitionOutputConfig(
            inline_response_config=cloud_speech.InlineOutputConfig()),
    )

def run():
    demand_identity()
    paid=os.environ.get("GOLDEN_V2_CHIRP_PAID_ENABLED","").lower()=="true"
    if paid and os.environ.get("GITHUB_RUN_ATTEMPT")!="1":
        raise RuntimeError("Chirp paid attempts may not rerun")
    from google.cloud import storage
    from google.cloud.speech_v2 import SpeechClient
    from google.api_core.client_options import ClientOptions
    storage_client=storage.Client(project=PROJECT)
    bucket=storage_client.bucket(BUCKET)
    prior_cents,prior_sha=verify_input_and_primary(bucket)
    uri=f"gs://{BUCKET}/{SOURCE_KEY}"
    request=build_request(uri)
    assert request.config.model==MODEL and len(request.files)==1
    assert request.config.features.enable_word_time_offsets
    print("CHIRP_3_INPUT_AND_PRIMARY_RECEIPT_VERIFIED=true")
    if not paid:
        print("CHIRP_3_SPEECH_V2_REAL_SDK_REQUEST_SHAPE=PASS")
        print("CHIRP_3_MODEL_REQUESTS_SENT=0")
        return
    # The cloud-side immutable claim is committed before the billable submit.
    claim={
        "schema":"golden-v2-paid-chirp3-claim-v1",
        "source_sha256":SOURCE_SHA,"model":MODEL,
        "language_code":"cmn-Hant-TW",
        "stage_cap_cents":STAGE_CAP_CENTS,
        "aggregate_cap_cents":TOTAL_CAP_CENTS,
        "prior_primary_estimated_cents":prior_cents,
        "prior_primary_receipt_sha256":prior_sha,
        "owner_approval":"chat-20261013-four-minute-USD10",
        "start_utc":datetime.now(timezone.utc).isoformat(),
        "workflow_run_id":os.environ.get("GITHUB_RUN_ID"),
        "workflow_ref":WORKFLOW_REF,
        "status":"REQUEST_STARTED_NO_RESUBMIT",
    }
    write_once(bucket,CHIRP_CLAIM,claim)
    print("CHIRP_3_IMMUTABLE_CLOUD_CLAIM=COMMITTED")
    client=SpeechClient(client_options=ClientOptions(
        api_endpoint="us-speech.googleapis.com"))
    operation=client.batch_recognize(request=request,retry=None,timeout=60)
    op=getattr(getattr(operation,"operation",None),"name",None)
    if not isinstance(op,str) or not op.startswith((
        f"projects/{PROJECT}/locations/us/operations/",
        "projects/608343797618/locations/us/operations/",
    )):
        raise RuntimeError("operation id not available: manual reconcile")
    write_once(bucket,CHIRP_OP,{
        "schema":"golden-v2-chirp3-operation-v1",
        "claim_key":CHIRP_CLAIM,"model":MODEL,
        "source_sha256":SOURCE_SHA,
        "operation_name":op,
        "status":"SUBMITTED_WAIT_FOR_RESULT",
    })
    print("CHIRP_3_OPERATION_NAME_DURABLY_SAVED=true")
    response=operation.result(timeout=540)
    seconds=duration_seconds(getattr(response,"total_billed_duration",None))
    cents=estimate_cents(seconds)
    item=response.results.get(uri) if hasattr(response.results,"get") else response.results[uri]
    err=getattr(item,"error",None)
    if item is None or int(getattr(err,"code",0) or 0):
        raise RuntimeError("Chirp result missing/error: manual reconcile")
    results=getattr(getattr(item,"transcript",None),"results",[]) or []
    parts=[];words=[]
    for part in results:
        if not part.alternatives: continue
        alt=part.alternatives[0]
        parts.append(str(alt.transcript or ""))
        for word in alt.words:
            start=duration_seconds(word.start_offset)
            end=duration_seconds(word.end_offset)
            words.append({"word":str(word.word or ""),"start_seconds":start,
                         "end_seconds":end})
    transcript="".join(parts).strip()
    valid_words=bool(words) and all(
        bool(w["word"]) and 0<=w["start_seconds"]<w["end_seconds"]<=241
        for w in words)
    passed=(bool(transcript) and valid_words
            and cents<=STAGE_CAP_CENTS
            and prior_cents+cents<=TOTAL_CAP_CENTS)
    from google.cloud.speech_v2.types import cloud_speech
    raw=cloud_speech.BatchRecognizeResponse.to_dict(
        response,preserving_proto_field_name=True)
    receipt={
        "schema":"golden-v2-paid-chirp3-response-v1",
        "model":MODEL,"source_sha256":SOURCE_SHA,"claim_key":CHIRP_CLAIM,
        "operation_name":op,"status":"COMPLETE" if passed else "REVIEW_REQUIRED",
        "transcript":transcript,"words":words,
        "word_time_offsets_verified":valid_words,
        "billed_duration_seconds":seconds,
        "estimated_cents":cents,
        "stage_cap_cents":STAGE_CAP_CENTS,
        "aggregate_cap_cents":TOTAL_CAP_CENTS,
        "prior_primary_receipt_sha256":prior_sha,
        "raw_response":raw,
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
    }
    write_once(bucket,CHIRP_RECEIPT,receipt)
    print("CHIRP_3_IMMUTABLE_PROVIDER_RECEIPT=CREATED")
    print("CHIRP_3_RECOGNIZED_CHARS="+str(len(transcript)))
    print("CHIRP_3_ACOUSTIC_WORDS="+str(len(words)))
    print("CHIRP_3_ESTIMATE_CENTS="+str(cents))
    print("CHIRP_3_STATUS="+receipt["status"])
    if not passed:
        raise RuntimeError("Chirp result requires review, do not automatically retry")
    print("CHIRP_3_ONE_SHOT=SUCCESS")

if __name__=="__main__":
    try:
        run()
    except Exception as exc:
        print("CHIRP_3_STOP_ERROR_TYPE="+type(exc).__name__,file=sys.stderr)
        print("CHIRP_3_MANUAL_RECONCILIATION_REQUIRED=true",file=sys.stderr)
        sys.exit(1)
