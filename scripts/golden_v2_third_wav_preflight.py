"""Verify WAV transcription inputs and official Gemini 3.5 SDK request, WITHOUT model calls."""
from __future__ import annotations

import hashlib
import io
import json
import wave

BUCKET="xenon-chain-506409-c3-golden-v2-smoke"
SRC="17db78ae930d28be3de930467ac256166de54fa62bb2e9cd361cdb58587d5621"
WAV_SHA="b199e203f5500f92ea9c2962ca7e20497b94fad737b28775bae7c52480568447"
PREFIX=f"golden-v2-smoke/{SRC}"
MODEL="gemini-3.5-transcribe"

def run():
    from google.cloud import storage
    from google.genai import types
    storage_client=storage.Client(project="xenon-chain-506409-c3")
    bucket=storage_client.bucket(BUCKET)
    raw=bucket.blob(PREFIX+"/clip-16000Hz-mono.wav").download_as_bytes(retry=None,timeout=30)
    if len(raw)!=7680156 or hashlib.sha256(raw).hexdigest()!=WAV_SHA:
        raise RuntimeError("WAV bytes do not match approved four-minute lossless derivative")
    with wave.open(io.BytesIO(raw),"rb") as w:
        if not(w.getframerate()==16000 and w.getnchannels()==1 and
               w.getsampwidth()==2 and w.getnframes()==3840000):
            raise RuntimeError("WAV must be 240s 16kHz mono PCM16")
    previous=[]
    for name,model in [
        ("primary-gemini38.receipt.json","gemini-3.8-flash"),
        ("chirp3.receipt.json","chirp_3")
    ]:
        receipt=json.loads(bucket.blob(PREFIX+"/paid/"+name).download_as_bytes(retry=None,timeout=20))
        if not(receipt.get("source_sha256")==SRC and receipt.get("model")==model
               and receipt.get("status")=="COMPLETE"):
            raise RuntimeError("prior paid stage evidence not confirmed")
        previous.append(model)
    old=json.loads(bucket.blob(PREFIX+"/paid/third-gemini35.claim.json").download_as_bytes(
        retry=None,timeout=20))
    if not(old.get("source_sha256")==SRC and old.get("model")==MODEL
           and old.get("status")=="REQUEST_STARTED_NO_RETRY"):
        raise RuntimeError("previous failed claim must remain immutable")
    config=types.GenerateContentConfig(
        audio_transcription_config=types.AudioTranscriptionConfig(
            mode="VERBATIM",word_timestamp=False,language_codes=["zh-TW"]
        )
    )
    audio=types.Part.from_uri(
        file_uri=f"gs://{BUCKET}/{PREFIX}/clip-16000Hz-mono.wav",
        mime_type="audio/wav")
    if(audio.file_data.mime_type!="audio/wav" or
       config.audio_transcription_config.mode!="VERBATIM" or
       config.audio_transcription_config.language_codes!=["zh-TW"]):
        raise RuntimeError("incompatible SDK request schema")
    print("GEMINI35_WAV_APPROVED_SOURCE_SHA=VERIFIED")
    print("GEMINI35_WAV_240_SEC_16KHZ_MONO=VERIFIED")
    print("GEMINI35_PRIOR_TWO_PROVIDERS=VERIFIED")
    print("GEMINI35_PREVIOUS_FAILED_CLAIM=FROZEN")
    print("GEMINI35_OFFICIAL_WAV_VERBATIM_REQUEST=READY")
    print("GEMINI35_MODEL_CALLS=0")
if __name__=="__main__":
    run()
