# Deployment status — 2026-07-31

Source:

- Branch: `agent/frontend-api-integration`
- Commit: `5d4ed4d`
- GitHub: Draft PR #2
- VPS root: `/opt/course-transcript`

Verified:

- 24/24 Python tests pass locally and inside the Oracle ARM64 image.
- Next.js production build passes; production npm audit reports zero
  vulnerabilities.
- API, preflight worker, approved pipeline worker, frontend, and the existing
  Cloudflare connector are running.
- Only the frontend publishes a host port, bound to `127.0.0.1:3300`.
- GCP credential and rclone mounts are read-only and limited by service role.
- Non-paid fake-provider E2E passes timeline, fixed segments, correction,
  multi-format export, QA, output validation, usage and final processing
  manifest. The full isolated worker reached `awaiting_review` with 16/16
  canonical artifacts on both the development host and the deployed ARM64
  image. The ARM64 run used `--network none`, proving zero cloud requests.
- The deployed ARM64 image passes all 24 Python tests.
- SQLite test state survived a four-service restart; the test row was removed.
- Frontend same-origin proxy reached the real API and performed a read-only
  Drive root browse; no paid operation started.
- Unauthenticated production request receives a Cloudflare Access redirect.
- No IAM, DNS, firewall, Cloudflare route/policy, Billing, Drive upload, or
  paid provider request was changed or initiated.

Backup:

- `/opt/course-transcript/backups/pre-pipeline-5d4ed4d.tar.gz`
- Excludes secrets, persistent data, logs, temp data, Git metadata, and build
  caches. Existing SQLite/data were never overwritten by the backup.

Pending human gates:

1. Refresh the already authenticated production browser and confirm Dashboard
   and `新增轉錄任務` render.
2. Select one small Drive media file and run only non-paid preflight.
3. Report the chosen source, exact estimate, Chirp 3 + Gemini 3.6 Flash, and
   whether to authorize the first paid five-minute request.
4. Keep Drive upload disabled until a later explicit approval.
