# Codex Handoff: Frontend Integration

## Current state

This branch contains a responsive visual prototype built with Next.js, React, TypeScript, plain CSS, and Lucide icons.

Current frontend characteristics:

- Dashboard, new-job form, and job-review workspace
- Deterministic mock data in `lib/mock-data.ts`
- No service-account access
- No direct GCS, Google Drive, Chirp, or Gemini calls
- No backend mutations
- Mock interactions for job creation, audio playback state, transcript selection, tabs, and terminology review

The frontend is intentionally isolated from the existing Oracle VPS worker until the API contract is implemented.

## Required first actions

Run and preserve the existing prototype:

```bash
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

Do not begin by rewriting the interface. Integrate the real backend incrementally and retain the existing status model and review workflow.

## Important validation note

The code was created through the GitHub integration. The assistant environment could not complete `npm install` because its internal npm mirror did not expose scoped `@types/*` packages. This is an environment limitation, not a known application error.

Codex must run `npm install` and `npm run build` on the VPS or a normal development machine before merging. Fix any TypeScript or package compatibility issues in the feature branch and document them in the PR.

## Recommended repository structure

Keep the Python transcription system and this frontend in one repository during MVP development:

```text
course-transcript-mvp/
├── app/                       # Next.js routes
├── components/                # Next.js UI components
├── lib/                       # frontend types, API client, adapters
├── docs/
├── backend/                   # migrate current FastAPI/Python app here
│   ├── app/
│   ├── tests/
│   └── pyproject.toml
├── Dockerfile.frontend
├── docker-compose.yml
└── README.md
```

Do not move production job data, logs, temporary audio, or GCP secrets into Git.

## Integration sequence

### Milestone 1: Read-only job API

Create these FastAPI endpoints first:

```text
GET /api/v1/health
GET /api/v1/jobs
GET /api/v1/jobs/{job_id}
GET /api/v1/jobs/{job_id}/segments
GET /api/v1/jobs/{job_id}/review-terms
GET /api/v1/jobs/{job_id}/artifacts
```

Frontend work:

- Add `lib/api-client.ts`
- Read `NEXT_PUBLIC_API_BASE_URL`
- Replace dashboard mock jobs with `GET /jobs`
- Replace job-page data with job, segment, term, and artifact endpoints
- Add explicit loading, empty, unavailable, and error states
- Keep `lib/mock-data.ts` as an explicit development fallback only

### Milestone 2: Job creation

Add:

```text
POST /api/v1/jobs
POST /api/v1/drive/inspect
```

Suggested request:

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

The `drive/inspect` endpoint should return safe file metadata only. It must never return OAuth tokens, rclone configuration, GCS credentials, or local secret paths.

### Milestone 3: Live progress

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

Stop polling for terminal states: `completed`, `review`, or `failed`.

Do not add WebSockets or server-sent events until queue state and persistence are stable.

### Milestone 4: Human review mutations

Add optimistic-concurrency-safe endpoints:

```text
PATCH /api/v1/jobs/{job_id}/segments/{segment_id}
PATCH /api/v1/jobs/{job_id}/review-terms/{term_id}
POST  /api/v1/jobs/{job_id}/review/complete
```

Each mutable resource should have a `revision` integer. Reject stale updates with HTTP 409.

Example segment patch:

```json
{
  "corrected_text": "修正後文字",
  "expected_revision": 3
}
```

The normal correction endpoint must not permit the browser to change `start_ms`, `end_ms`, source chunk ownership, or raw Chirp text.

### Milestone 5: Audio and waveform

Use WaveSurfer.js only after a secure audio endpoint exists.

Recommended endpoint:

```text
GET /api/v1/jobs/{job_id}/audio
```

Requirements:

- Support HTTP range requests
- Authorize before serving content
- Do not expose a permanent public GCS URL
- Prefer backend streaming or a short-lived signed URL
- Keep original Drive credentials server-side

Replace the current visual waveform mock in `components/job-detail-page.tsx` with a dedicated client component.

### Milestone 6: Artifacts and Drive publishing

Add:

```text
GET  /api/v1/jobs/{job_id}/artifacts/{artifact_id}/download
POST /api/v1/jobs/{job_id}/publish-to-drive
```

Drive publishing must be refused unless:

- Pipeline outputs passed automated QA
- Job is in `review` or `completed`
- All required terms are resolved
- User explicitly confirms destination and non-overwrite behavior

Return the final Drive path and a read-back checksum after publishing.

## API and frontend conventions

The Python API may return snake_case. Add a mapping layer in `lib/api-client.ts`; do not spread raw API response objects through React components.

Extend the status and domain types in `lib/types.ts`. Do not create a second incompatible enum.

For transcript segments, preserve this invariant:

```text
segment_id, order, start_ms and end_ms are immutable
corrected_text and review metadata are mutable
```

## Backend security boundaries

The browser must never receive or access:

- `/opt/course-transcript/secrets/gcp-sa.json`
- Google service-account JSON content
- rclone configuration content
- permanent signed URLs
- GCP operation credentials
- arbitrary VPS filesystem paths

The frontend container does not need the GCP service-account volume. Only backend worker containers may mount it read-only.

## Docker Compose target

A later integration can follow this separation:

```yaml
services:
  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    environment:
      NEXT_PUBLIC_API_BASE_URL: /api
    ports:
      - "3000:3000"
    depends_on:
      - api

  api:
    build: ./backend
    volumes:
      - /opt/course-transcript/data:/opt/course-transcript/data
      - /opt/course-transcript/logs:/opt/course-transcript/logs

  worker:
    build: ./backend
    volumes:
      - /opt/course-transcript/data:/opt/course-transcript/data
      - /opt/course-transcript/logs:/opt/course-transcript/logs
      - /opt/course-transcript/secrets/gcp-sa.json:/run/secrets/gcp-sa.json:ro
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /run/secrets/gcp-sa.json
```

Use an internal Docker network and a reverse proxy in production. Do not expose the worker or database publicly.

## Acceptance criteria for the first integration PR

- `npm run build` passes on ARM64 VPS and a standard x86 development machine
- Dashboard loads real read-only job data
- Job detail loads real pipeline, segments, review terms, and artifacts
- Loading, empty, API error, and missing-job states exist
- No writes are enabled in the first API PR
- No secret is copied, logged, serialized, or exposed to the browser
- Layout remains usable at 375 px, 768 px, and 1440 px widths
- Mock data is available only behind an explicit development flag
- Integration choices are documented in the PR description

## Files to understand first

```text
components/app-shell.tsx
components/dashboard-page.tsx
components/new-job-page.tsx
components/job-detail-page.tsx
lib/types.ts
lib/mock-data.ts
app/globals.css
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
