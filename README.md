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

1. Read a user-approved Google Drive audio file through rclone.
2. Normalize with FFmpeg.
3. Split to ≤15-minute Chirp chunks with 10-second overlap.
4. Run Chirp 3 BatchRecognize with GCS output and word timestamps.
5. Merge words by midpoint ownership boundaries.
6. Build fixed subtitle segments; Gemini may only return corrected text for the
   existing IDs, order, and timestamps.
7. Generate QA and stop for review before any Drive upload.

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
