# Deployment

Production root: `/opt/course-transcript` on Oracle Ubuntu 24.04 ARM64.

1. Back up the current source and database.
2. Sync the reviewed branch without deleting VPS-only secrets/data.
3. Confirm there are no approved queued jobs before starting a newly built
   pipeline worker.
4. Build and start `api`, `worker`, `pipeline-worker`, and `frontend` with the
   existing Compose/Cloudflare overlay.
5. Do not recreate or alter the existing tunnel, Access application, DNS, IAM,
   firewall, or Billing.
6. Run tests inside the ARM64 API image, inspect service health/logs, confirm
   the frontend is bound only to `127.0.0.1:3300`, and verify restart
   persistence.

The Cloudflare route targets `http://frontend:3000`. No service-account or
rclone secret is available to the frontend. Drive upload is absent by design.

Rollback uses the pre-deployment archive plus the preserved SQLite database.
Never use destructive Git reset or remove the project/data directory.
