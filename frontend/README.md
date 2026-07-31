# Course Transcript Frontend

Responsive Next.js prototype for the long-form course transcription platform.

The interface reflects the backend workflow already implemented in this repository:

```text
Google Drive → rclone → FFmpeg → GCS → Chirp 3 → Gemini → QA → export
```

## Screens

- `/` — dashboard and recent jobs
- `/jobs/new` — browse Drive and create a one-file, multi-file, or folder batch
- `/batches/[id]` — monitor preflight and explicitly confirm estimated cost
- `/jobs/voice-11386603-seg1` — audio, transcript, QA, term review, and artifacts

The current implementation uses deterministic mock data. It does not access Google Drive, GCS, GCP credentials, Chirp, Gemini, or the worker directly.

## Local development

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

## Build

```bash
npm run build
npm start
```

## Docker

```bash
docker build -t course-transcript-frontend ./frontend
docker run --rm -p 3000:3000 \
  -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
  course-transcript-frontend
```

## Integration handoff

Read the repository-level documents:

- `docs/FRONTEND_DESIGN_SPEC.md`
- `docs/CODEX_FRONTEND_HANDOFF.md`

Drive browsing is proxied through authenticated, same-origin FastAPI endpoints.
The browser receives only item metadata, never the service-account key, rclone
configuration, permanent GCS credentials, or direct Google Drive access.
