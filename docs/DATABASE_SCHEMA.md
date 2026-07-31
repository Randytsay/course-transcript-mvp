# Database schema

SQLite WAL is stored under the persistent `data` volume.

- `batch_previews` / `source_previews`: expiring, immutable Drive selections.
- `batches`: aggregate item/duration/cost/status/revision.
- `jobs`: source metadata, state, progress, estimate/reservation/usage,
  approval, revision, global lease, and heartbeat.
- `job_events`: append-only actor/payload audit history.
- `stage_runs`: one durable row per stage with attempt count/checksum/times.
- `usage_records`: provider/model units and estimated cost, idempotent by
  `(job_id, dedupe_key)`.

Raw provider payloads and large derived artifacts remain files indexed by job;
the database stores state and audit metadata, not private transcript bodies.
