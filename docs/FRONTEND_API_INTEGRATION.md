# Frontend and API integration

The user interface is connected to a **read-only** FastAPI boundary. This first
integration milestone makes real worker state visible without letting a browser
create a paid job, alter transcript content, read audio, access Drive, or
publish results.

## Routes

| Route | Source | Notes |
|---|---|---|
| `GET /api/v1/health` | controlled API state | no provider credentials |
| `GET /api/v1/jobs` | `data/jobs/*` manifests/reports | derived, safe summary |
| `GET /api/v1/jobs/{job_id}` | controlled job directory | no arbitrary path traversal |
| `GET /api/v1/jobs/{job_id}/segments` | subtitle JSON | stable IDs/order/times and raw/corrected text |
| `GET /api/v1/jobs/{job_id}/review-terms` | optional review export | empty until candidates exist |
| `GET /api/v1/jobs/{job_id}/artifacts` | explicit allow-list | metadata only; no downloads yet |

The API never returns a GCS URI, operation name, rclone configuration,
service-account contents, or a filesystem path.

## Run locally

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
COURSE_TRANSCRIPT_DATA_DIR="$PWD/data" .venv/bin/uvicorn app.api:app --reload

cd frontend
printf 'NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1\n' > .env.local
npm install
npm run dev
```

## Run on the VPS without public exposure

The Compose `web` profile binds the frontend to `127.0.0.1:3000`; the API has
no host port and is reachable only over the Docker network. Do not expose it
through a reverse proxy until authentication and a reviewed access policy exist.

```bash
docker compose --profile web up -d --build api frontend
curl http://127.0.0.1:3000/api/v1/health
```

## Explicitly deferred

- creating/queuing jobs and Drive inspection
- mutable transcript or terminology review
- audio streaming/waveform
- artifact downloads
- Drive publishing
- login and public reverse proxy

Each requires a separate security and workflow review.
