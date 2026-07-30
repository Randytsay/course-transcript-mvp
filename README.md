# Course Transcript MVP

Responsive frontend prototype for an AI-assisted long-form course transcription platform.

The interface is designed around the production workflow being built on Oracle VPS:

```text
Google Drive → rclone → FFmpeg → GCS → Chirp 3 → Gemini → QA → export
```

## Included screens

- Dashboard and recent jobs
- Create transcription job
- Job progress and pipeline status
- Audio review workspace
- Transcript segments and uncertain-term review
- QA and export controls

The current branch uses deterministic mock data. It deliberately does **not** access Google Drive, GCP credentials, or the transcription worker directly.

## Run locally

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

Prototype routes:

```text
/
/jobs/new
/jobs/voice-11386603-seg1
```

## Production build

```bash
npm run build
npm start
```

## Docker

```bash
docker build -f Dockerfile.frontend -t course-transcript-frontend .
docker run --rm -p 3000:3000 \
  -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
  course-transcript-frontend
```

## Codex handoff

Start with:

- [`docs/CODEX_HANDOFF.md`](docs/CODEX_HANDOFF.md)
- [`docs/FRONTEND_DESIGN_SPEC.md`](docs/FRONTEND_DESIGN_SPEC.md)

The first integration milestone is read-only FastAPI data. Do not connect the browser directly to Google Drive, GCS, Chirp, Gemini, rclone, or the service-account key.
