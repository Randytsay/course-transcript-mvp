# Runbook

## Safety rules

- Never print, copy, or commit the service-account JSON.
- Keep `/opt/course-transcript/secrets/gcp-sa.json` outside Git and mount read-only.
- Never overwrite Drive source media.
- Do not send whole long media to Chirp when word timestamps are enabled.
- After user authorization for this policy, the production pipeline may upload
  only the selected derived formats after local QA passes, beside the source
  file. It never overwrites source media; it keeps `awaiting_review` for
  vocabulary review.
- Do not print, commit, or copy a Cloudflare Tunnel token. Its VPS file must
  remain `/opt/course-transcript/secrets/cloudflare-tunnel.env`, owned by root
  with mode `600`.
- Do not publish a Cloudflare hostname or change DNS until Cloudflare Access
  has an explicit Allow policy approved by the user.

## Cloudflare Tunnel connector

The connector is defined separately in `docker-compose.cloudflare.yml` so it
does not alter the existing web services. It has no host port, no GCP
credential mount, and reaches the frontend only through the Docker network.

Start or inspect it on the VPS without printing the token:

```bash
sudo docker compose --env-file /opt/course-transcript/secrets/cloudflare-tunnel.env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile tunnel up -d cloudflared
sudo docker compose --env-file /opt/course-transcript/secrets/cloudflare-tunnel.env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  ps cloudflared
sudo docker compose --env-file /opt/course-transcript/secrets/cloudflare-tunnel.env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  logs --tail=50 cloudflared
```

Expected first result: the tunnel reports healthy connections in Cloudflare;
no public route exists yet. After the user creates a Cloudflare Access
application, a published hostname may map only to `http://frontend:3000`.
Never map the hostname directly to `api:8000`.

## Chirp result parsing

Use GCS output only. The first Chirp request is a 120-second serial canary;
only after its GCS result validates may the normal 15-minute chunks run in
parallel. Check `file_result.error`, inspect
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

The production Chirp pipeline now uses this behaviour by default: it submits
each operation once, then polls the private GCS output prefix serially until
the result object appears. It does not call the Speech long-running-operation
endpoint while waiting. A `SUBMITTED` chunk is therefore recoverable evidence,
not a reason to submit the same audio again.

## Current validated local exports

Run `python -m app.providers.validate_outputs` in the worker container after
the following generators complete:

- `merge_chunks`, `build_srt`, `qa_report`
- `correct_text` (Gemini 3.6 Flash, text-only)
- `export_formats`

Validated artifacts include raw and corrected SRT/VTT/ASS, structured JSON,
TXT, Markdown, CSV, DOCX, PDF, terminology evidence, merge decisions, join QA,
raw provider responses, JSON/HTML QA, usage, and checksummed processing
manifest. Google Docs and Drive upload remain intentionally disabled.

## Chinese subtitle segmentation

Run `build_srt` only after merging the Chirp word timeline. The builder uses
`jieba` locally to map character-level ASR timings to Chinese lexical units,
then chooses boundaries at real speech gaps, punctuation (including ASR ASCII
punctuation), and safe cue lengths. Do not reintroduce a fixed-duration split
that can cut `時` from `間`, or split separate jieba tokens that map to the
same Chirp word timing; the latter can create a zero-duration cue. Gemini
receives the resulting fixed cues for text-only correction and must not change
their timing.

## Web batch preflight

Set these non-secret production values in `/opt/course-transcript/.env`:

```dotenv
COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS=true
COURSE_TRANSCRIPT_PUBLIC_ORIGIN=https://transcript.randy88.ccwu.cc
COURSE_TRANSCRIPT_COST_LIMIT_USD=200
COURSE_TRANSCRIPT_COST_WARNING_THRESHOLDS_USD=50,100,160,190
RCLONE_CONFIG_HOST_PATH=/home/ubuntu/.config/rclone/rclone.conf
```

The rclone config remains a host secret and is mounted read-only at
`/run/secrets/rclone.conf`. Do not print its contents. Start the private web
stack with:

```bash
sudo docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile web --profile tunnel up -d --build
```

Creating a batch performs only read-only listing and local preflight.
`awaiting_confirmation` means no paid API call has started. The operator must
open `/batches/{id}`, review the exact estimate, check the authorization box,
and confirm. Exceeding the application US$200 estimate cap is rejected inside
the same SQLite transaction.

At batch creation, the web UI stores the requested user-facing attachments in
each job's `output_formats_json`. The default is `srt`, `txt`, and `csv`; VTT,
ASS, DOCX, and PDF are optional. This selection controls future download/
Drive-publication choices only. JSON, raw provider responses, word timelines,
manifests, and QA evidence remain private VPS artifacts regardless of that
selection and must not be deleted merely because they are not selected.

## Explicit Drive publication recovery

The current production policy is automatic post-QA publication to the source
file's own Drive folder. Set `COURSE_TRANSCRIPT_AUTO_PUBLISH_TO_SOURCE=false`
to disable it for a deployment. It uploads only the job's selected formats and
uses the same one-job rate-safe `app.jobs.drive_publish` implementation.

The publisher uploads one selected attachment at a time with `rclone copyto
--checksum`, a one-request-per-second ceiling, bounded retries, and a
30/60/120-second backoff (with small jitter) for `rateLimitExceeded`. It writes
`drive-publish-state.json` beside the job artifacts after every attempt. A
completed file is not sent again when the command is resumed; a successful
copy must also pass a precise remote-size read-back before it is recorded as
completed. The state file never records an rclone response, OAuth material, or
other secrets.

Use the read-only rclone config mount. A message that rclone could not save a
refreshed token is not proof of either success or failure: rely on the command
exit status and the recorded remote-size read-back. If the publisher ends in
`rate_limited`, it stops after the bounded retry window and records resumable
state. Do not repeatedly list the Drive folder or re-run any paid
transcription stage; resume only the publication step later.

Before deployment, run:

```bash
python -m unittest discover -s tests -v
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev
```

The production `pipeline-worker` stays idle until a revision-checked cost
approval changes a job to `queued`. To exercise the same deterministic stages
without cloud cost, set `COURSE_TRANSCRIPT_FAKE_PROVIDER=1` only on a dedicated
test worker/job, then remove it before production. Never use health alone as
proof; verify state transitions, artifacts, read-back hashes, restart
persistence, and logs.

Pause/resume and failed-stage retry are available on the job page. Pause is
observed at the next worker heartbeat; completed evidence remains intact.
Retry resumes only the recorded failed stage and downstream stages—output
failure never invalidates completed ASR evidence.
