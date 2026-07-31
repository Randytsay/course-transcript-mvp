# API

All production mutations require Cloudflare Access identity and exact Origin.

- `POST /api/v1/drive/browse`: one-level private Drive listing.
- `POST /api/v1/drive/preview-batch`: one/many files or one recursive folder.
- `POST /api/v1/batches`: non-paid preflight batch.
- `GET /api/v1/batches/{id}`: status, jobs, durations, estimate, revision.
- `POST /api/v1/batches/{id}/approve`: exact estimate/revision authorization.
- `GET /api/v1/jobs`, `/jobs/{id}`: live job state.
- `GET /api/v1/jobs/{id}/events`: durable stage/audit events.
- `POST /api/v1/jobs/{id}/pause|resume`: revision-controlled operation.
- `POST /api/v1/jobs/{id}/retry-stage`: recorded failed stage only.
- `GET /api/v1/jobs/{id}/segments|review-terms|artifacts`: review data.
- `PATCH /api/v1/jobs/{id}/review-terms/{term_id}`: record a human decision
  without modifying raw transcript.
- `GET /api/v1/jobs/{id}/artifacts/{name}`: allowlisted derived file only.

Approval authorizes the queue; it is not proof that a provider request already
started. Drive upload endpoints are intentionally absent.
