# Course Transcript MVP

Private, resumable long-audio transcription MVP. Chirp 3 provides word-level
timing; Google Vertex AI Gemini 3.6 Flash performs text-only correction after
the timing layer is complete.

## Security

- The service-account JSON remains outside this repository at /opt/course-transcript/secrets/gcp-sa.json.
- Docker mounts it only as /run/secrets/gcp-sa.json:ro.
- Tests create only test-prefixed GCS objects and clean them up.
- The rclone check records only the root-folder item count, never names.
- Cloudflare Tunnel credentials are stored only at
  `/opt/course-transcript/secrets/cloudflare-tunnel.env` on the VPS. They are
  not copied into this repository, image, logs, or chat.

## Current workflow

1. After Cloudflare Access login, browse one Drive directory level at a time.
2. Select one file, multiple files, or one folder recursively (maximum 100
   supported media files per batch).
3. Create a zero-cost preflight batch. The sequential worker copies one source
   at a time, runs checksum/FFprobe, records an estimated cost, and removes the
   temporary copy.
4. The user must explicitly confirm the whole batch estimate before any paid
   transcription can be queued.
5. Read the approved Google Drive source through rclone and normalize with FFmpeg.
6. Split to ≤15-minute Chirp chunks with 10-second overlap.
7. Run Chirp 3 BatchRecognize with GCS output and word timestamps.
8. Merge words by midpoint ownership boundaries.
9. Build fixed subtitle segments; Gemini may only return corrected text for the
   existing IDs, order, and timestamps.
10. Generate QA and stop for review before any Drive upload.

The web batch is a queue, not parallel source processing: only one source job
may hold an active worker lease. There is no scheduled Drive scan.

## Batch API and safety gates

- `POST /api/v1/drive/browse`: authenticated, read-only single-directory listing.
- `POST /api/v1/drive/preview-batch`: validates explicit files or recursively
  expands one folder, then creates a 30-minute preview.
- `POST /api/v1/batches`: creates only local preflight jobs.
- `GET /api/v1/batches/{id}`: returns child jobs, duration, status and estimate.
- `POST /api/v1/batches/{id}/approve`: revision- and exact-cost-guarded approval.

Production mutations require Cloudflare Access identity headers and the exact
`https://transcript.randy88.ccwu.cc` Origin. Browser responses never contain
rclone configuration or permanent credentials.

## Validated local output set

For a completed job, `app.providers.export_formats` produces a local-only
export manifest and these review artifacts:

- raw and corrected `.srt`, `.vtt`, `.ass`
- raw and corrected structured JSON
- raw, timestamped, and corrected `.txt` / `.md`
- `transcript-segments.csv` and global terminology CSV/JSON
- raw Chirp evidence, merge decisions, ±10-second join QA, and QA reports

Run `python -m app.providers.validate_outputs` after QA. It checks immutable
segment timing, subtitle structures, CSV row counts, raw provider evidence,
Gemini 3.6 Flash correction records, glossary, and join QA without modifying
files. Google Docs/DOCX/PDF and Drive upload are deliberately outside this
local-review milestone and require separate OAuth plus explicit approval.

Read [ARCHITECTURE.md](ARCHITECTURE.md), [RUNBOOK.md](RUNBOOK.md), and
[HANDOVER.md](HANDOVER.md) before changing or running the pipeline.

## Private web access through Cloudflare Tunnel

The review frontend remains loopback-only. `docker-compose.cloudflare.yml`
adds an outbound-only `cloudflared` connector on the same Docker network, with
no published host port and no access to the GCP service-account key.

On the VPS, after a root-owned tunnel token has been placed in
`/opt/course-transcript/secrets/cloudflare-tunnel.env`, start it with:

```bash
sudo docker compose --env-file /opt/course-transcript/secrets/cloudflare-tunnel.env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile tunnel up -d cloudflared
```

Do not add a public hostname until a Cloudflare Access application with an
explicit Allow policy is in place. The published route will target
`http://frontend:3000` inside the Docker network; the API remains unexposed.

## Phase 1 run

Copy .env.example to .env, then build and run the infrastructure-test Compose service. Run scripts/rclone_readonly_test.sh and then scripts/render_infrastructure_report.py. The final report is logs/infrastructure-test-report.md.
