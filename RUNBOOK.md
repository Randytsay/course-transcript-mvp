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
895, 1785, and 2675 seconds: earlier chunk keeps midpoint `< boundary`; later
chunk keeps midpoint `>= boundary`.
