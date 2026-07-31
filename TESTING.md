# Testing

Local acceptance:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m unittest discover -s tests -v
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev
git diff --check
```

Coverage includes source path validation, multi-file/folder selection,
preflight, revision/cost gates, global lease and heartbeat, usage
idempotency, pause/resume/retry, Access/Origin checks, immutable review
decisions, chunk boundaries, Chinese lexical subtitle segmentation, pipeline
stop-at-review, and checksummed DOCX/PDF exports.

Docker/VPS acceptance must additionally prove ARM64 image builds, fake-provider
E2E, Compose health, SQLite persistence across container restart, loopback-only
ports, unauthenticated Access redirect, authenticated frontend/API access, and
an idle paid worker with zero approved queued jobs. A health response alone is
not end-to-end proof.

The isolated full worker check is:

```bash
python -m app.pipeline.fake_e2e
```

It uses a temporary SQLite database and six-second generated audio, forces the
fake providers, requires `awaiting_review` plus 16/16 canonical artifacts, and
deletes the temporary directory. It has no credential dependency and makes
zero cloud requests.

The first real five-minute request is a separate acceptance gate and requires
the user's explicit source, exact estimate, model confirmation, and approval.
