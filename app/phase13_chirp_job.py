"""Create the authoritative Chirp word timeline for the user-approved long file."""
from __future__ import annotations
import json, os
from pathlib import Path
from google.cloud import speech_v2, storage
from google.cloud.speech_v2.types import cloud_speech
ROOT=Path('/app'); JOB=ROOT/'data'/'jobs'/'voice_11386603-seg1'
def ms(x): return round(x.total_seconds()*1000)
def main():
 project=os.environ['GOOGLE_CLOUD_PROJECT']; bucket_name=os.environ['GCS_BUCKET']; bucket=storage.Client().bucket(bucket_name); obj='jobs/voice_11386603-seg1/normalized.flac'; blob=bucket.blob(obj)
 if not blob.exists(): blob.upload_from_filename(JOB/'normalized.flac',content_type='audio/flac')
 client=speech_v2.SpeechClient(client_options={'api_endpoint':'us-speech.googleapis.com'})
 config=cloud_speech.RecognitionConfig(auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),language_codes=['cmn-Hans-CN'],model='chirp_3',features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True))
 uri=f'gs://{bucket_name}/{obj}'
 op=client.batch_recognize(request=cloud_speech.BatchRecognizeRequest(recognizer=f'projects/{project}/locations/us/recognizers/_',config=config,files=[cloud_speech.BatchRecognizeFileMetadata(uri=uri)],recognition_output_config=cloud_speech.RecognitionOutputConfig(inline_response_config=cloud_speech.InlineOutputConfig())))
 response=op.result(timeout=3600); data=response.results[uri].transcript
 words=[]
 for r in data.results:
  for w in r.alternatives[0].words: words.append({'word':w.word,'start_ms':ms(w.start_offset),'end_ms':ms(w.end_offset)})
 (JOB/'chirp-words.json').write_text(json.dumps({'model':'chirp_3','words':words},ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(f'PHASE13_CHIRP=PASS words={len(words)}')
if __name__=='__main__': main()
