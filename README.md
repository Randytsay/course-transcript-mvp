# Course Transcript MVP

Private, resumable long-audio transcription MVP. Chirp 3 provides word-level
timing; Google Vertex AI Gemini 3.6 Flash performs text-only correction after
the timing layer is complete.

## Security

- The service-account JSON remains outside this repository at /opt/course-transcript/secrets/gcp-sa.json.
- Docker mounts it only as /run/secrets/gcp-sa.json:ro.
- Tests create only test-prefixed GCS objects and clean them up.
- The rclone check records only the root-folder item count, never names.

## Current workflow

1. Read a user-approved Google Drive audio file through rclone.
2. Normalize with FFmpeg.
3. Split to ≤15-minute Chirp chunks with 10-second overlap.
4. Run Chirp 3 BatchRecognize with GCS output and word timestamps.
5. Merge words by midpoint ownership boundaries.
6. Build fixed subtitle segments; Gemini may only return corrected text for the
   existing IDs, order, and timestamps.
7. Generate QA and stop for review before any Drive upload.

Read [ARCHITECTURE.md](ARCHITECTURE.md), [RUNBOOK.md](RUNBOOK.md), and
[HANDOVER.md](HANDOVER.md) before changing or running the pipeline.

## Phase 1 run

Copy .env.example to .env, then build and run the infrastructure-test Compose service. Run scripts/rclone_readonly_test.sh and then scripts/render_infrastructure_report.py. The final report is logs/infrastructure-test-report.md.
