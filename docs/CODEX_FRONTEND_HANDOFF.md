# Codex Handoff: Frontend Integration

## Current state

The `frontend/` directory contains a responsive visual prototype built with Next.js, React, TypeScript, plain CSS, and Lucide icons.

Current characteristics:

- Dashboard, new-job form, and job-review workspace
- Deterministic mock data in `frontend/lib/mock-data.ts`
- No service-account access
- No direct GCS, Google Drive, Chirp, Gemini, or rclone calls
- No backend mutations
- Mock interactions for job creation, audio playback state, transcript selection, tabs, and terminology review

The frontend is deliberately isolated from the Oracle VPS worker until the API contract is implemented.

## Required first actions

Run and preserve the existing prototype:

```bash
cd frontend
npm install
npm run build
npm run dev
```

Verify these routes before changing the design:

```text
/
/jobs/new
/jobs/voice-11386603-seg1
```

Do not start by rewriting the interface. Integrate the backend incrementally and retain the existing status model and human-review workflow.

## Validation note

The prototype was written through the GitHub integration. The assistant execution environment could not complete `npm install` because its internal npm mirror did not expose scoped `@types/*` packages. This is an environment limitation, not a confirmed application defect.

Codex must run `npm install` and `npm run build` on the VPS or a normal development machine before merge. Fix any TypeScript or dependency compatibility issue on this feature branch and document it in the PR.

## Repository boundaries

Keep the existing Python transcription pipeline at repository root. Keep the web application under `frontend/`.

Do not move production job data, logs, temporary audio, rclone configuration, or GCP secrets into Git.

## Milestone 1: Read-only API

Implement these FastAPI endpoints first:

```text
GET /api/v1/health
GET /api/v1/jobs
GET /api/v1/jobs/{job_id}
GET /api/v1/jobs/{job_id}/segments
GET /api/v1/jobs/{job_id}/review-terms
GET /api/v1/jobs/{job_id}/artifacts
```

Frontend work:

- Add `frontend/lib/api-client.ts`
- Read `NEXT_PUBLIC_API_BASE_URL`
- Replace dashboard mock jobs with `GET /jobs`
- Replace job-detail data with job, segments, terms, and artifacts endpoints
- Add loading, empty, unavailable, not-found, and API-error states
- Keep `mock-data.ts` only behind an explicit development flag

Do not enable writes in this milestone.

## Milestone 2: Job creation

Add:

```text
POST /api/v1/jobs
POST /api/v1/drive/inspect
```

Suggested create request:

```json
{
  "source_path": "gdrive:.../voice_11386603-seg1.mp3",
  "language_code": "cmn-Hant-TW",
  "profile": "highest_accuracy",
  "enable_gemini_correction": true,
  "enable_subtitles": true,
  "require_human_review": true
}
```

Suggested response:

```json
{
  "job_id": "voice-11386603-seg1-20260731-001",
  "status": "queued",
  "created_at": "2026-07-31T01:40:00+08:00"
}
```

`drive/inspect` returns safe metadata only. It must not return OAuth tokens, rclone configuration, GCS credentials, service-account fields, or secret filesystem paths.

## Milestone 3: Live progress

Start with polling every 5 seconds:

```text
GET /api/v1/jobs/{job_id}
```

Required fields:

```json
{
  "status": "transcribing",
  "progress": 48,
  "active_stage": "chirp",
  "stage_detail": "chunk 2 of 4",
  "updated_at": "...",
  "error": null
}
```

Stop polling for terminal states: `completed`, `review`, and `failed`.

Do not add WebSockets or server-sent events until queue state and persistence are stable.

## Milestone 4: Human review mutations

Add optimistic-concurrency-safe endpoints:

```text
PATCH /api/v1/jobs/{job_id}/segments/{segment_id}
PATCH /api/v1/jobs/{job_id}/review-terms/{term_id}
POST  /api/v1/jobs/{job_id}/review/complete
```

Every mutable resource should include a `revision` integer. Reject stale writes with HTTP 409.

Example segment patch:

```json
{
  "corrected_text": "修正後文字",
  "expected_revision": 3
}
```

The ordinary correction endpoint must not let the browser change `start_ms`, `end_ms`, source chunk ownership, or raw Chirp text.

## Milestone 5: Audio and waveform

Use WaveSurfer.js only after a secure audio endpoint exists:

```text
GET /api/v1/jobs/{job_id}/audio
```

Requirements:

- Support HTTP range requests
- Authorize before serving audio
- Do not expose a permanent public GCS URL
- Prefer backend streaming or a short-lived signed URL
- Keep Google Drive credentials server-side

Replace the current visual waveform mock in `frontend/components/job-detail-page.tsx` with a dedicated client component.

## Milestone 6: Artifacts and Drive publishing

Add:

```text
GET  /api/v1/jobs/{job_id}/artifacts/{artifact_id}/download
POST /api/v1/jobs/{job_id}/publish-to-drive
```

Publishing must be rejected unless:

- Automated QA passed
- Job status is `review` or `completed`
- All required terms are resolved
- User confirms destination and non-overwrite behavior

Return the final Drive path and a read-back checksum after publishing.

## API conventions

The Python API may use snake_case. Add a mapping layer in `frontend/lib/api-client.ts`; do not spread raw response objects through React components.

Extend `frontend/lib/types.ts`; do not introduce a second incompatible status enum.

Transcript invariant:

```text
segment_id, order, start_ms, end_ms, and raw Chirp text are immutable
corrected_text and review metadata are mutable
```

## Security boundaries

The browser must never receive or access:

- `/opt/course-transcript/secrets/gcp-sa.json`
- Service-account JSON content
- rclone configuration content
- Permanent signed URLs
- GCP operation credentials
- Arbitrary VPS filesystem paths

The frontend container does not need the GCP service-account volume. Only the backend worker may mount it read-only.

## Docker Compose target

```yaml
services:
  frontend:
    build: ./frontend
    environment:
      NEXT_PUBLIC_API_BASE_URL: /api
    ports:
      - "3000:3000"
    depends_on:
      - api

  api:
    build: .
    volumes:
      - /opt/course-transcript/data:/opt/course-transcript/data
      - /opt/course-transcript/logs:/opt/course-transcript/logs

  worker:
    build: .
    volumes:
      - /opt/course-transcript/data:/opt/course-transcript/data
      - /opt/course-transcript/logs:/opt/course-transcript/logs
      - /opt/course-transcript/secrets/gcp-sa.json:/run/secrets/gcp-sa.json:ro
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /run/secrets/gcp-sa.json
```

Use an internal Docker network and reverse proxy in production. Do not expose the worker or database publicly.

## Acceptance criteria for the first integration PR

- `npm run build` passes on the ARM64 VPS and a standard development machine
- Dashboard loads real read-only job data
- Job detail loads real pipeline, segments, review terms, and artifacts
- Loading, empty, API-error, and missing-job states exist
- No frontend writes are enabled yet
- No secret is copied, logged, serialized, or exposed to the browser
- Layout is usable at 375 px, 768 px, and 1440 px widths
- Mock data is available only behind an explicit development flag
- Integration choices are documented in the PR description

## Files to understand first

```text
frontend/components/app-shell.tsx
frontend/components/dashboard-page.tsx
frontend/components/new-job-page.tsx
frontend/components/job-detail-page.tsx
frontend/lib/types.ts
frontend/lib/mock-data.ts
frontend/app/globals.css
docs/FRONTEND_DESIGN_SPEC.md
```

## Explicit non-goals for the first integration

- No login system
- No multi-tenant permissions
- No WebSockets
- No direct browser-to-GCS upload
- No direct browser-to-Google Drive access
- No automatic Drive publishing
- No production database migration without a reviewed schema
- No rewrite of the Chirp/Gemini pipeline while integrating the UI
