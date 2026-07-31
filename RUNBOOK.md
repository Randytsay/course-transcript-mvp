# Runbook

## Safety rules

- Never print, copy, or commit the service-account JSON.
- Keep `/opt/course-transcript/secrets/gcp-sa.json` outside Git and mount read-only.
- Never overwrite Drive source media.
- Do not send whole long media to Chirp when word timestamps are enabled.
- Do not auto-upload final files to Drive; obtain user approval after QA.

## Chirp result parsing

Use GCS output only. Check `file_result.error`, inspect
`file_result._pb.WhichOneof("result")`, require `cloud_storage_result`, and
read `native_format_uri` with `uri` compatibility fallback. Do not use
deprecated top-level `uri` or `transcript` fields.

## Merge rule

Use word midpoint ownership, not text similarity. For this job, boundaries are
derived from actual adjacent Chirp coverage. Earlier chunk keeps midpoint
`< boundary`; later chunk keeps midpoint `>= boundary`. Do not treat silence
as a transcription failure. A zero-word chunk is only retryable after a
separate speech/VAD check confirms it contains speech.

## Operation recovery

When `BatchRecognize` operation polling returns 429, do not resubmit the
audio. Persist the operation name, wait, and run `app.providers.recover_chunk`
to recover the single GCS result object. Set `CHUNK_ROLE=patch` explicitly for
a targeted repair; the recovery script preserves that role. `SUBMIT_ONLY=1`
can create the operation without consuming polling quota.

## Current validated local exports

Run `python -m app.providers.validate_outputs` in the worker container after
the following generators complete:

- `merge_chunks`, `build_srt`, `qa_report`
- `correct_text` (Gemini 3.6 Flash, text-only)
- `export_formats`

Validated artifacts include raw and corrected SRT/VTT/ASS, structured JSON,
TXT, Markdown, segment CSV, terminology CSV/JSON, merge decisions, join QA,
raw Chirp evidence, raw Gemini responses, and QA reports. `Google Docs`,
`DOCX`, and `PDF` are intentionally not generated in this local-review stage:
they require a separate Drive/Docs OAuth setup and explicit upload approval.
